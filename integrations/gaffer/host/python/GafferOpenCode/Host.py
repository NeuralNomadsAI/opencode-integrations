import collections
import datetime
import fnmatch
import hashlib
import json
import math
import os
import pathlib
import re
import secrets
import socket
import tempfile
import threading
import time
import uuid

import IECore
import IECoreScene
import Gaffer
import GafferDispatch
import GafferImage
import GafferScene
import GafferUI

from . import Recipes


PROTOCOL_VERSION = 1
HOST_VERSION = json.loads( ( pathlib.Path( __file__ ).resolve().parents[2] / "manifest.json" ).read_text( encoding = "utf-8" ) )["version"]
MAX_REQUEST_BYTES = 1024 * 1024
JOURNAL_LIMIT = 256
COMMAND_LIMIT = 256
TOMBSTONE_LIMIT = 4096
OPERATIONS = (
	"node.create",
	"node.rename",
	"plug.set_value",
	"graph.connect",
	"graph.connect_many",
	"graph.delete",
	"output.define",
	"render.start",
	"render.cancel",
	"ui.view",
)
METHODS = ( "hello", "context", "sync", "apply", "cancel", "resume" )


class HostError( RuntimeError ) :

	def __init__( self, code, message, data = None ) :

		RuntimeError.__init__( self, message )
		self.code = code
		self.data = data or {}


def _jsonValue( value ) :

	if isinstance( value, float ) and not math.isfinite( value ) :
		return "NaN" if math.isnan( value ) else ( "Infinity" if value > 0 else "-Infinity" )
	if value is None or isinstance( value, ( bool, int, float, str ) ) :
		return value
	if isinstance( value, dict ) :
		return { str( key ) : _jsonValue( item ) for key, item in value.items() }
	if isinstance( value, ( list, tuple ) ) :
		return [ _jsonValue( item ) for item in value ]
	if hasattr( value, "value" ) :
		return _jsonValue( value.value )
	try :
		return [ _jsonValue( item ) for item in value ]
	except TypeError :
		return repr( value )


def _valuesEqual( observed, expected ) :

	if isinstance( observed, ( int, float ) ) and not isinstance( observed, bool ) and isinstance( expected, ( int, float ) ) and not isinstance( expected, bool ) :
		return math.isclose( float( observed ), float( expected ), rel_tol = 1e-6, abs_tol = 1e-7 )
	if isinstance( observed, list ) and isinstance( expected, list ) :
		return len( observed ) == len( expected ) and all( _valuesEqual( a, b ) for a, b in zip( observed, expected ) )
	if isinstance( observed, dict ) and isinstance( expected, dict ) :
		return observed.keys() == expected.keys() and all( _valuesEqual( observed[key], expected[key] ) for key in observed )
	return observed == expected


def _parameterData( value ) :

	if isinstance( value, bool ) :
		return IECore.BoolData( value )
	if isinstance( value, int ) :
		return IECore.IntData( value )
	if isinstance( value, float ) and math.isfinite( value ) :
		return IECore.FloatData( value )
	if isinstance( value, str ) :
		return IECore.StringData( value )
	raise HostError( "type_mismatch", "Output parameters must be finite numbers, booleans, or strings" )


def _boxValue( box ) :

	return {
		"min" : [ int( box.min().x ), int( box.min().y ) ],
		"max" : [ int( box.max().x ), int( box.max().y ) ],
	}


def _imageFeedback( path, previewDirectory, previewStem ) :

	result = {
		"path" : path,
		"exists" : os.path.isfile( path ),
	}
	if not result["exists"] :
		return result
	result["size_bytes"] = os.path.getsize( path )
	try :
		reader = GafferImage.ImageReader()
		reader["fileName"].setValue( path )
		if not reader["fileValid"].getValue() :
			result["valid_image"] = False
			return result
		result["valid_image"] = True
		image = reader["out"]
		format = image.format()
		displayWindow = format.getDisplayWindow()
		dataWindow = image.dataWindow()
		channels = list( image.channelNames() )
		result.update( {
			"display_window" : _boxValue( displayWindow ),
			"data_window" : _boxValue( dataWindow ),
			"width" : displayWindow.size().x,
			"height" : displayWindow.size().y,
			"pixel_aspect" : format.getPixelAspect(),
			"channels" : channels,
			"image_hash" : str( GafferImage.ImageAlgo.imageHash( image ) ),
		} )

		statsChannels = channels[:4]
		if statsChannels :
			stats = GafferImage.ImageStats()
			stats["in"].setInput( image )
			stats["channels"].setValue( IECore.StringVectorData( statsChannels ) )
			stats["areaSource"].setValue( GafferImage.ImageStats.AreaSource.DisplayWindow )
			result["statistics"] = {
				"channels" : statsChannels,
				"minimum" : _jsonValue( stats["min"].getValue() )[:len( statsChannels )],
				"maximum" : _jsonValue( stats["max"].getValue() )[:len( statsChannels )],
				"average" : _jsonValue( stats["average"].getValue() )[:len( statsChannels )],
			}

		width = max( 1, int( result["width"] ) )
		height = max( 1, int( result["height"] ) )
		scale = min( 1.0, 512.0 / max( width, height ) )
		previewWidth = max( 1, int( round( width * scale ) ) )
		previewHeight = max( 1, int( round( height * scale ) ) )
		previewPath = ( previewDirectory / ( previewStem + ".png" ) ).as_posix()
		resize = GafferImage.Resize()
		resize["in"].setInput( image )
		resize["format"].setValue( GafferImage.Format( previewWidth, previewHeight, 1.0 ) )
		resize["fitMode"].setValue( GafferImage.Resize.FitMode.Fit )
		writer = GafferImage.ImageWriter()
		writer["in"].setInput( resize["out"] )
		writer["fileName"].setValue( previewPath )
		writer["task"].execute()
		if os.path.isfile( previewPath ) :
			result["preview_path"] = previewPath
	except Exception as error :
		result["feedback_error"] = str( error )
	return result


class _RenderJob :

	def __init__( self, jobID, sessionID, sourceRevision, targetRef, targetPath, name, frameRange, outputs, dispatcher, localJob ) :

		self.id = jobID
		self.sessionID = sessionID
		self.sourceRevision = sourceRevision
		self.targetRef = targetRef
		self.targetPath = targetPath
		self.name = name
		self.frameRange = frameRange
		self.outputs = outputs
		self.dispatcher = dispatcher
		self.localJob = localJob
		self.submittedAt = datetime.datetime.now( datetime.timezone.utc )
		self.started = time.monotonic()
		self.__feedbackComplete = False
		self.__lock = threading.Lock()
		self.__monitor = threading.Thread( target = self.__monitorJob, name = "GafferOpenCodeRender", daemon = True )
		self.__monitor.start()

	def cancel( self ) :

		self.localJob.kill()

	def active( self ) :

		return self.localJob.status() in ( self.localJob.Status.Waiting, self.localJob.Status.Running ) or not self.__feedbackComplete

	def card( self, after = 0, limit = 50 ) :

		status = self.localJob.status()
		if status == self.localJob.Status.Waiting :
			state = "queued"
		elif status == self.localJob.Status.Running or status == self.localJob.Status.Complete and not self.__feedbackComplete :
			state = "running"
		elif status == self.localJob.Status.Complete :
			state = "succeeded"
		elif status == self.localJob.Status.Killed :
			state = "cancelled"
		else :
			state = "failed"

		messages = list( self.localJob.messages() )
		after = max( 0, int( after ) )
		limit = max( 1, min( int( limit ), 200 ) )
		selected = messages[after:after + limit]
		feedback = [
			{
				"sequence" : after + index + 1,
				"level" : str( message.level ).lower(),
				"context" : str( message.context ),
				"message" : str( message.message ),
			}
			for index, message in enumerate( selected )
		]
		with self.__lock :
			outputs = [ dict( output ) for output in self.outputs ]
		result = {
			"id" : self.id,
			"state" : state,
			"phase" : "feedback" if status == self.localJob.Status.Complete and not self.__feedbackComplete else "render",
			"source_revision" : self.sourceRevision,
			"target" : { "ref" : self.targetRef, "path" : self.targetPath },
			"name" : self.name,
			"frame_range" : self.frameRange,
			"submitted_at" : self.submittedAt.isoformat().replace( "+00:00", "Z" ),
			"elapsed_ms" : round( ( time.monotonic() - self.started ) * 1000 ),
			"outputs" : outputs,
			"feedback" : {
				"next" : after + len( selected ),
				"truncated" : after + len( selected ) < len( messages ),
				"messages" : feedback,
			},
		}
		if state == "failed" :
			errors = [ item for item in feedback if item["level"] in ( "error", "fatal" ) ]
			if errors :
				result["error"] = errors[-1]["message"]
		return result

	def __monitorJob( self ) :

		while self.localJob.status() in ( self.localJob.Status.Waiting, self.localJob.Status.Running ) :
			time.sleep( 0.05 )
		if self.localJob.status() == self.localJob.Status.Complete :
			previewDirectory = pathlib.Path( tempfile.gettempdir() ) / "opencode" / "gaffer-previews"
			previewDirectory.mkdir( parents = True, exist_ok = True )
			for index, output in enumerate( self.outputs ) :
				feedback = _imageFeedback( output["path"], previewDirectory, self.id.replace( ":", "_" ) + "_" + str( index ) )
				with self.__lock :
					self.outputs[index].update( feedback )
		self.__feedbackComplete = True


def _children( component, childType = None ) :

	if childType is None :
		return list( component.children() )
	return list( component.children( childType ) )


def _walk( component ) :

	yield component
	for child in _children( component ) :
		yield from _walk( child )


def _walkPlugs( node ) :

	for plug in _children( node, Gaffer.Plug ) :
		yield plug
		yield from _walkPlugChildren( plug )


def _walkPlugChildren( plug ) :

	for child in _children( plug, Gaffer.Plug ) :
		yield child
		yield from _walkPlugChildren( child )


class ScriptState :

	def __init__( self, script, recipes = None ) :

		self.script = script
		self.recipes = recipes or Recipes.Registry()
		self.handle = "script:" + secrets.token_urlsafe( 18 )
		self.epoch = str( uuid.uuid4() )
		self.revision = 1
		self.quarantined = False
		self.__cancellation = {}
		self.__trackingSuppressed = False
		self.__refsByObject = {}
		self.__objectsByRef = {}
		self.__tombstones = collections.OrderedDict()
		self.__cards = {}
		self.__connections = []
		self.__connectedObjects = set()
		self.__dirtyNodes = set()
		self.__removedRefs = set()
		self.__summaryDirty = False
		self.__flushScheduled = False
		self.__idleFlushCallback = self.__flushFromIdle
		self.__journal = collections.deque( maxlen = JOURNAL_LIMIT )
		self.__commands = collections.OrderedDict()
		self.__sideEffectCommands = {}
		self.__recent = collections.deque( maxlen = 12 )
		self.__renderJobs = collections.OrderedDict()

		self.__registerTree( script )
		self.__rebuildAllCards()
		self.__connectSelection()

	def close( self ) :

		for job in self.__renderJobs.values() :
			if job.active() :
				job.cancel()
		if self.__flushScheduled :
			GafferUI.EventLoop.removeIdleCallback( self.__idleFlushCallback )
			self.__flushScheduled = False
		self.__connections = []
		self.__objectsByRef = {}
		self.__tombstones = collections.OrderedDict()
		self.__refsByObject = {}
		self.__cards = {}
		self.__renderJobs = collections.OrderedDict()

	def hello( self ) :

		self.flush()
		nodeTypes = self.recipes.nodeTypes()
		nodeRecipes = self.recipes.catalogue()
		capabilities = {
			"methods" : list( METHODS ),
			"operations" : [ { "name" : name, "undoable" : name not in ( "render.start", "render.cancel", "ui.view" ) } for name in OPERATIONS ],
			"undo" : "transactional",
			"background" : False,
		}
		return {
			"protocol" : PROTOCOL_VERSION,
			"host_version" : HOST_VERSION,
			"app_version" : Gaffer.About.versionString(),
			"document" : pathlib.Path( self.script["fileName"].getValue() ).name[:120] or "Untitled",
			"capabilities" : capabilities,
			"script_handle" : self.handle,
			"script_epoch" : self.epoch,
			"revision" : self.revision,
			"capability_hash" : hashlib.sha256( json.dumps( capabilities, sort_keys = True, separators = ( ",", ":" ) ).encode( "utf-8" ) ).hexdigest(),
			"operations" : list( OPERATIONS ),
			"node_types" : list( nodeTypes ),
			"node_recipes" : nodeRecipes,
			"summary" : self.summary(),
		}

	def summary( self ) :

		selection = []
		for node in self.script.selection() :
			selection.append( self.__ref( node ) )
		focus = self.script.getFocus()
		with self.script.context() :
			fileName = str( self.script["fileName"].getValue() )
		return {
			"script" : {
				"name" : self.script.getName(),
				"file_name" : fileName,
				"node_count" : len( self.__cards ),
			},
			"selection" : selection,
			"focus" : self.__ref( focus ) if focus is not None else None,
			"recent" : [ ref for ref in self.__recent if ref in self.__objectsByRef ],
		}

	def context( self, query ) :

		self.flush()
		requestedRevision = query.get( "if_revision" )
		if requestedRevision is not None and int( requestedRevision ) > self.revision :
			raise HostError(
				"revision_conflict",
				"Requested revision is newer than the Gaffer graph",
				{ "current_revision" : self.revision },
			)

		limit = max( 1, min( int( query.get( "limit", 20 ) ), 100 ) )
		include = set( query.get( "include", [] ) )
		plugPatterns = query.get( "plugs" )
		if plugPatterns is not None and (
			not isinstance( plugPatterns, list ) or any( not isinstance( pattern, str ) or not pattern for pattern in plugPatterns )
		) :
			raise HostError( "invalid_request", "plugs must contain non-empty glob patterns" )
		plugLimit = max( 1, min( int( query.get( "plug_limit", 100 ) ), 1000 ) )
		sessionID = str( query.get( "session_id", "" ) )
		jobQuery = query.get( "job" )
		if jobQuery is not None and not isinstance( jobQuery, dict ) :
			raise HostError( "invalid_request", "job must be an object" )
		nodes = []
		seen = set()
		truncated = [ False ]

		def addNode( node ) :
			if node is None or not isinstance( node, Gaffer.Node ) or node.isSame( self.script ) or not self.__belongsToScript( node ) :
				return
			ref = self.__ref( node )
			if ref not in seen :
				if len( nodes ) >= limit :
					truncated[0] = True
					return
				seen.add( ref )
				nodes.append( node )

		for ref in query.get( "refs", [] ) :
			component = self.__resolve( ref )
			addNode( component if isinstance( component, Gaffer.Node ) else component.ancestor( Gaffer.Node ) )

		match = query.get( "match" ) or {}
		if match :
			namePattern = str( match.get( "name", "*" ) )
			typePattern = str( match.get( "type", "*" ) )
			for ref, card in self.__cards.items() :
				if fnmatch.fnmatchcase( card["name"], namePattern ) and fnmatch.fnmatchcase( card["type"], typePattern ) :
					addNode( self.__objectsByRef.get( ref ) )
					if truncated[0] :
						break

		nonGraphOnly = ( query.get( "recipe_match" ) is not None or jobQuery is not None ) and not query.get( "refs" ) and not match
		if not nodes and not nonGraphOnly :
			for node in self.script.selection() :
				addNode( node )
			addNode( self.script.getFocus() )

		if not nodes and not nonGraphOnly :
			for node in _children( self.script, Gaffer.Node ) :
				addNode( node )
				if len( nodes ) >= limit :
					break

		for unused in range( max( 0, min( int( query.get( "radius", 0 ) ), 2 ) ) ) :
			for node in list( nodes ) :
				for neighbour in self.__neighbours( node ) :
					addNode( neighbour )

		entities = [
			self.__nodeCard(
				node,
				includeValues = "values" in include,
				includeConnections = "connections" in include,
				includeSchema = "schema" in include,
				plugPatterns = plugPatterns,
				plugLimit = plugLimit,
			)
			for node in nodes
		]
		if "affordances" in include :
			for entity in entities :
				entity["affordances"] = list( OPERATIONS )

		result = {
			"script_handle" : self.handle,
			"revision" : self.revision,
			"capability_hash" : self.recipes.capabilityHash( OPERATIONS, METHODS ),
			"summary" : self.summary(),
			"entities" : entities,
			"truncated" : truncated[0],
		}
		if "capabilities" in include :
			result["operations"] = list( OPERATIONS )
			result["node_types"] = list( self.recipes.nodeTypes() )
		recipeMatch = query.get( "recipe_match" )
		if recipeMatch is not None and not isinstance( recipeMatch, dict ) :
			raise HostError( "invalid_request", "recipe_match must be an object" )
		if "recipes" in include or recipeMatch is not None :
			allMatchingRecipes = self.recipes.catalogue( recipeMatch, limit + 1 )
			result["node_recipes"] = allMatchingRecipes[:limit]
			result["recipes_truncated"] = len( allMatchingRecipes ) > limit
		if jobQuery is not None :
			jobID = jobQuery.get( "id" )
			if not isinstance( jobID, str ) or not jobID :
				raise HostError( "invalid_request", "job.id must be a non-empty string" )
			job = self.__renderJobs.get( jobID )
			if job is None or job.sessionID != sessionID :
				raise HostError( "unknown_job", "Unknown render job for this session" )
			result["job"] = job.card( jobQuery.get( "after", 0 ), jobQuery.get( "limit", 50 ) )
		if "renders" in include :
			result["jobs"] = [
				job.card( 0, 1 ) for job in reversed( self.__renderJobs.values() )
				if job.sessionID == sessionID
			][:20]
		return result

	def sync( self, afterRevision ) :

		self.flush()
		afterRevision = int( afterRevision or 0 )
		if afterRevision == self.revision :
			return { "revision" : self.revision, "changes" : [], "summary" : self.summary() }

		changes = [ item for item in self.__journal if item["revision"] > afterRevision ]
		if changes and changes[0]["previous_revision"] <= afterRevision :
			return { "revision" : self.revision, "changes" : changes, "summary" : self.summary() }

		return {
			"revision" : self.revision,
			"changes" : [],
			"reset" : True,
			"snapshot" : self.context( { "limit" : 100 } ),
			"summary" : self.summary(),
		}

	def apply( self, request, cancellationGeneration, requestWasCancelled ) :

		self.flush()
		sessionID = request.get( "session_id" )
		if not isinstance( sessionID, str ) or not sessionID :
			raise HostError( "invalid_intent", "session_id is required" )
		if requestWasCancelled or self.__cancellationState( sessionID )[1] or cancellationGeneration != self.__cancellationState( sessionID )[0] :
			raise HostError( "cancelled", "The current OpenCode turn was interrupted" )
		if self.quarantined :
			raise HostError( "script_quarantined", "Remote writes are disabled until the script is reattached" )

		commandID = str( request.get( "command_id", "" ) )
		if not commandID :
			raise HostError( "invalid_intent", "command_id is required" )
		requestDigest = hashlib.sha256( json.dumps( request, sort_keys = True ).encode( "utf-8" ) ).hexdigest()
		previous = self.__sideEffectCommands.get( commandID ) or self.__commands.get( commandID )
		if previous is not None :
			if previous["digest"] != requestDigest :
				raise HostError( "command_id_reused", "command_id was already used for another intent" )
			if not previous.get( "side_effect" ) and self.revision != previous["result"]["revision"] :
				raise HostError(
					"revision_conflict",
					"The graph changed after this command_id was applied",
					{ "command_revision" : previous["result"]["revision"], "current_revision" : self.revision },
				)
			return previous["result"]

		expectedRevision = int( request.get( "if_revision", -1 ) )
		if expectedRevision != self.revision :
			raise HostError(
				"revision_conflict",
				"The Gaffer graph changed after the intent was prepared",
				{ "expected_revision" : expectedRevision, "current_revision" : self.revision },
			)

		operations = request.get( "operations" )
		if not isinstance( operations, list ) or not operations or len( operations ) > 50 :
			raise HostError( "invalid_intent", "operations must contain between 1 and 50 items" )

		plans, aliases = self.__preflight( operations, sessionID )
		renderPlans = [ plan for plan in plans if plan["kind"] in ( "render_start", "render_cancel" ) ]
		if renderPlans and ( len( plans ) != 1 or request.get( "expect" ) ) :
			raise HostError( "invalid_intent", "Render start and cancel must each be the sole operation with no postconditions" )
		if renderPlans :
			before = self.revision
			payload = self.__executeRenderPlan( renderPlans[0], sessionID )
			result = {
				"status" : "accepted" if renderPlans[0]["kind"] == "render_start" else "cancelled",
				"script_handle" : self.handle,
				"revision" : self.revision,
				"revision_before" : before,
				"command_id" : commandID,
				"summary" : self.summary(),
				"jobs" : [ payload ],
			}
			self.__sideEffectCommands[commandID] = { "digest" : requestDigest, "result" : result, "side_effect" : True }
			return result
		self.__preverify( request.get( "expect", [] ), plans, aliases )
		before = self.revision
		actionCount = [ 0 ]
		def actionSlot( unusedScript, unusedAction, stage ) :
			if stage == Gaffer.Action.Stage.Do :
				actionCount[0] += 1
		actionConnection = self.script.actionSignal().connect( actionSlot, scoped = True )
		try :
			self.__trackingSuppressed = True
			with Gaffer.UndoScope( self.script ) :
				for plan in plans :
					if plan["kind"] == "view" :
						continue
					if requestWasCancelled or self.__cancellationState( sessionID )[1] or cancellationGeneration != self.__cancellationState( sessionID )[0] :
						raise HostError( "cancelled", "The current OpenCode turn was interrupted" )
					self.__executePlan( plan )
			for plan in plans :
				if plan["kind"] == "view" :
					self.__executePlan( plan )

			if requestWasCancelled or self.__cancellationState( sessionID )[1] or cancellationGeneration != self.__cancellationState( sessionID )[0] :
				raise HostError( "cancelled", "The current OpenCode turn was interrupted" )
			verification = self.__verifyPlans( plans )
			verification.extend( self.__verify( request.get( "expect", [] ), aliases ) )
			self.__trackingSuppressed = False
			self.__publishPlans( plans )
			for component in aliases.values() :
				self.__markComponent( component )
			self.flush( cause = "opencode", commandID = commandID )
		except Exception as error :
			self.__trackingSuppressed = True
			if isinstance( error, HostError ) :
				failure = error
			else :
				failure = HostError( "execution_failed", str( error ) )

			if actionCount[0] :
				try :
					if not self.script.undoAvailable() :
						raise HostError( "rollback_failed", "The remote undo item was not available" )
					self.script.undo()
					self.__discardRedo()
					self.flush( cause = "rollback", commandID = commandID )
				except Exception as rollbackError :
					self.quarantined = True
					raise HostError( "rollback_failed", str( rollbackError ) )
			self.__trackingSuppressed = False
			raise HostError(
				"execution_failed_rolled_back" if actionCount[0] else "execution_failed",
				str( failure ),
				{ "cause" : failure.code, "current_revision" : self.revision },
			)

		result = {
			"status" : "applied",
			"script_handle" : self.handle,
			"revision" : self.revision,
			"revision_before" : before,
			"command_id" : commandID,
			"verification" : verification,
			"summary" : self.summary(),
			"aliases" : { name : self.__ref( component ) for name, component in aliases.items() },
			"created" : {
				name : {
					"ref" : self.__ref( component ),
					"name" : component.getName(),
					"path" : component.relativeName( self.script ),
					"type" : component.typeName(),
				}
				for name, component in aliases.items()
			},
			"defined_outputs" : [
				{
					"ref" : self.__ref( plan["plug"] ),
					"target" : self.__ref( plan["node"] ),
					"name" : plan["name"],
					"path" : plan["plug"].relativeName( self.script ),
				}
				for plan in plans if plan["kind"] == "define_output"
			],
			"views" : [
				{
					"target" : self.__ref( plan["node"] ),
					"renderer" : plan["renderer"],
					"device" : plan["device"],
					"available_devices" : plan.get( "available_devices", [] ),
				}
				for plan in plans if plan["kind"] == "view"
			],
		}
		self.__commands[commandID] = { "digest" : requestDigest, "result" : result }
		self.__commands.move_to_end( commandID )
		while len( self.__commands ) > COMMAND_LIMIT :
			self.__commands.popitem( last = False )
		return result

	def setCancelled( self, sessionID, cancelled ) :

		generation, unusedCancelled = self.__cancellationState( sessionID )
		if cancelled :
			generation += 1
			for job in self.__renderJobs.values() :
				if job.sessionID == sessionID and job.active() :
					job.cancel()
		self.__cancellation[sessionID] = ( generation, cancelled )

	def __cancellationState( self, sessionID ) :

		return self.__cancellation.get( sessionID, ( 0, False ) )

	def cancellationState( self, sessionID ) :

		return self.__cancellationState( sessionID )

	def __discardRedo( self ) :

		barrier = Gaffer.Node( "__opencodeRollbackBarrier" )
		self.__trackingSuppressed = True
		try :
			with Gaffer.UndoScope( self.script ) :
				self.script.addChild( barrier )
				self.script.removeChild( barrier )
		finally :
			self.__trackingSuppressed = False

	def flush( self, cause = "user", commandID = None ) :

		if not self.__dirtyNodes and not self.__removedRefs and not self.__summaryDirty :
			return False

		put = []
		for ref in sorted( self.__dirtyNodes ) :
			node = self.__objectsByRef.get( ref )
			if node is not None and isinstance( node, Gaffer.Node ) and self.__belongsToScript( node ) and not node.isSame( self.script ) :
				card = self.__nodeCard( node )
				self.__cards[ref] = card
				put.append( card )
				self.__recent.append( ref )

		previousRevision = self.revision
		self.revision += 1
		change = {
			"previous_revision" : previousRevision,
			"revision" : self.revision,
			"cause" : cause,
			"command_id" : commandID,
			"put" : put,
			"removed" : sorted( self.__removedRefs ),
		}
		self.__journal.append( change )
		self.__dirtyNodes.clear()
		self.__removedRefs.clear()
		self.__summaryDirty = False
		return True

	def __flushFromIdle( self ) :

		self.__flushScheduled = False
		self.flush()
		return False

	def __scheduleFlush( self ) :

		if self.__flushScheduled :
			return
		self.__flushScheduled = True
		GafferUI.EventLoop.addIdleCallback( self.__idleFlushCallback )

	def __ref( self, component ) :

		ref = self.__refsByObject.get( component )
		if ref is None :
			prefix = "plug" if isinstance( component, Gaffer.Plug ) else "node"
			if isinstance( component, Gaffer.ScriptNode ) :
				prefix = "script"
			ref = prefix + ":" + secrets.token_urlsafe( 12 )
			self.__refsByObject[component] = ref
			self.__objectsByRef[ref] = component
		return ref

	def __registerTree( self, component ) :

		for item in _walk( component ) :
			ref = self.__ref( item )
			self.__objectsByRef[ref] = item
			self.__tombstones.pop( ref, None )
			self.__removedRefs.discard( ref )
			if item in self.__connectedObjects :
				continue
			self.__connectedObjects.add( item )
			self.__connections.append( item.childAddedSignal().connect( self.__childAdded, scoped = True ) )
			self.__connections.append( item.childRemovedSignal().connect( self.__childRemoved, scoped = True ) )
			self.__connections.append( item.nameChangedSignal().connect( self.__nameChanged, scoped = True ) )
			self.__connections.append( item.childrenReorderedSignal().connect( self.__childrenReordered, scoped = True ) )
			if isinstance( item, Gaffer.Node ) :
				self.__connections.append( item.plugSetSignal().connect( self.__plugChanged, scoped = True ) )
				self.__connections.append( item.plugInputChangedSignal().connect( self.__plugChanged, scoped = True ) )

	def __connectSelection( self ) :

		selection = self.script.selection()
		self.__connections.append( selection.memberAddedSignal().connect( self.__selectionChanged, scoped = True ) )
		self.__connections.append( selection.memberRemovedSignal().connect( self.__selectionChanged, scoped = True ) )
		self.__connections.append( self.script.focusChangedSignal().connect( self.__focusChanged, scoped = True ) )

	def __rebuildAllCards( self ) :

		self.__cards = {}
		for component in _walk( self.script ) :
			if isinstance( component, Gaffer.Node ) and not component.isSame( self.script ) :
				self.__cards[self.__ref( component )] = self.__nodeCard( component )

	def __nodeCard(
		self,
		node,
		includeValues = False,
		includeConnections = False,
		includeSchema = False,
		plugPatterns = None,
		plugLimit = 250,
	) :

		plugs = []
		for plug in _walkPlugs( node ) :
			relativePlugPath = plug.relativeName( node )
			if plugPatterns and not any(
				fnmatch.fnmatchcase( relativePlugPath, pattern ) or
				fnmatch.fnmatchcase( plug.getName(), pattern ) or
				fnmatch.fnmatchcase( plug.relativeName( self.script ), pattern )
				for pattern in plugPatterns
			) :
				continue
			entry = {
				"ref" : self.__ref( plug ),
				"path" : plug.relativeName( self.script ),
				"name" : plug.getName(),
				"type" : plug.typeName(),
				"direction" : "in" if plug.direction() == Gaffer.Plug.Direction.In else "out",
			}
			inputPlug = plug.getInput()
			if inputPlug is not None and self.__belongsToScript( inputPlug ) :
				entry["input"] = self.__ref( inputPlug )
			if includeConnections :
				entry["outputs"] = [ self.__ref( output ) for output in plug.outputs() if self.__belongsToScript( output ) ]
			if includeSchema :
				entry["accepts_inputs"] = bool( plug.getFlags( Gaffer.Plug.Flags.AcceptsInputs ) )
				if isinstance( plug, Gaffer.ValuePlug ) :
					entry["settable"] = bool( plug.settable() )
					try :
						with self.script.context() :
							entry["default"] = _jsonValue( plug.defaultValue() )
					except Exception :
						pass
			if includeValues and isinstance( plug, Gaffer.ValuePlug ) and inputPlug is None and plug.direction() == Gaffer.Plug.Direction.In :
				try :
					with self.script.context() :
						entry["value"] = _jsonValue( plug.getValue() )
				except Exception as error :
					entry["value_error"] = str( error )
			plugs.append( entry )
			if len( plugs ) >= plugLimit :
				break

		parent = node.parent()
		return {
			"ref" : self.__ref( node ),
			"path" : node.relativeName( self.script ),
			"name" : node.getName(),
			"type" : node.typeName(),
			"parent" : self.__ref( parent ) if parent is not None else None,
			"plugs" : plugs,
			"plugs_truncated" : len( plugs ) >= plugLimit,
		}

	def __neighbours( self, node ) :

		result = []
		seen = set()
		for plug in _walkPlugs( node ) :
			connected = []
			if plug.getInput() is not None :
				connected.append( plug.getInput() )
			try :
				connected.extend( plug.outputs() )
			except Exception :
				pass
			for otherPlug in connected :
				otherNode = otherPlug.node()
				if otherNode is not None and self.__belongsToScript( otherNode ) and not otherNode.isSame( node ) and otherNode not in seen :
					seen.add( otherNode )
					result.append( otherNode )
		return result

	def __resolve( self, identifier, aliases = None ) :

		aliases = aliases or {}
		if not isinstance( identifier, str ) or not identifier :
			raise HostError( "invalid_operation", "Gaffer references must be non-empty strings" )
		if identifier.startswith( "$" ) :
			rootName, separator, relativePath = identifier.partition( "." )
			component = aliases.get( rootName[1:] )
			if component is None :
				raise HostError( "unknown_ref", "Unknown intent alias " + rootName )
			if separator :
				component = component.descendant( relativePath )
			if component is None :
				raise HostError( "unknown_ref", "Alias path does not exist: " + identifier )
			return component

		component = self.__objectsByRef.get( identifier )
		if component is None and "." in identifier :
			rootRef, unusedSeparator, relativePath = identifier.partition( "." )
			root = self.__objectsByRef.get( rootRef )
			if root is not None :
				component = root.descendant( relativePath )
		if component is None :
			component = self.script.descendant( identifier )
		if component is None :
			raise HostError( "unknown_ref", "Unknown Gaffer reference or path: " + identifier )
		return component

	def __belongsToScript( self, component ) :

		script = component if isinstance( component, Gaffer.ScriptNode ) else component.ancestor( Gaffer.ScriptNode )
		return script is not None and script.isSame( self.script )

	def __planned( self, component, aliases ) :

		for root in aliases.values() :
			current = component
			while current is not None :
				if current.isSame( root ) :
					return True
				current = current.parent()
		return False

	def __descendsFromAny( self, component, ancestors ) :

		current = component
		while current is not None :
			if any( current.isSame( ancestor ) for ancestor in ancestors ) :
				return True
			current = current.parent()
		return False

	def __checkMutableSubject( self, component, aliases, deleted ) :

		if self.__descendsFromAny( component, deleted ) :
			raise HostError( "invalid_operation_order", "An operation targets a node deleted earlier in the intent" )
		if not self.__belongsToScript( component ) and not self.__planned( component, aliases ) :
			raise HostError( "unknown_ref", "The target is not attached to this script" )

	def __readOnly( self, component ) :

		try :
			return bool( Gaffer.MetadataAlgo.readOnly( component ) )
		except Exception :
			return True

	def __preflight( self, operations, sessionID ) :

		plans = []
		aliases = {}
		deleted = []
		for operation in operations :
			if not isinstance( operation, dict ) :
				raise HostError( "invalid_operation", "Each operation must be an object" )
			kind = str( operation.get( "op", "" ) )

			if kind in ( "plug.set_value", "set_value" ) :
				plug = self.__resolve( operation.get( "target" ), aliases )
				self.__checkMutableSubject( plug, aliases, deleted )
				if not isinstance( plug, Gaffer.ValuePlug ) or plug.direction() != Gaffer.Plug.Direction.In :
					raise HostError( "type_mismatch", "set_value target must be an input ValuePlug" )
				if not plug.settable() or self.__readOnly( plug ) :
					raise HostError( "read_only", "The target plug cannot be edited" )
				if "value" not in operation :
					raise HostError( "invalid_operation", "set_value requires value" )
				plans.append( { "kind" : "set_value", "plug" : plug, "value" : self.__coerceValue( plug, operation.get( "value" ) ) } )

			elif kind in ( "node.rename", "rename" ) :
				component = self.__resolve( operation.get( "target" ), aliases )
				self.__checkMutableSubject( component, aliases, deleted )
				if not isinstance( component, Gaffer.Node ) or isinstance( component, Gaffer.ScriptNode ) :
					raise HostError( "type_mismatch", "node.rename requires a non-script Node" )
				if self.__readOnly( component ) :
					raise HostError( "read_only", "The target cannot be renamed" )
				name = operation.get( "name" )
				if not isinstance( name, str ) or not name :
					raise HostError( "invalid_operation", "node.rename requires a non-empty name" )
				plans.append( { "kind" : "rename", "component" : component, "name" : name } )

			elif kind in ( "graph.connect", "connect" ) :
				source = self.__resolve( operation.get( "source" ), aliases )
				destination = self.__resolve( operation.get( "destination" ), aliases )
				self.__checkMutableSubject( source, aliases, deleted )
				self.__checkMutableSubject( destination, aliases, deleted )
				if not isinstance( source, Gaffer.Plug ) or not isinstance( destination, Gaffer.Plug ) :
					raise HostError( "type_mismatch", "connect requires source and destination plugs" )
				if self.__readOnly( destination ) or not destination.acceptsInput( source ) :
					raise HostError( "invalid_connection", "The destination does not accept this source" )
				plans.append( { "kind" : "connect", "source" : source, "destination" : destination } )

			elif kind == "graph.connect_many" :
				array = self.__resolve( operation.get( "target" ), aliases )
				self.__checkMutableSubject( array, aliases, deleted )
				if not isinstance( array, Gaffer.ArrayPlug ) or array.direction() != Gaffer.Plug.Direction.In :
					raise HostError( "type_mismatch", "graph.connect_many target must be an input ArrayPlug" )
				if self.__readOnly( array ) :
					raise HostError( "read_only", "The target ArrayPlug cannot be edited" )
				sourceIDs = operation.get( "sources" )
				if not isinstance( sourceIDs, list ) or not sourceIDs :
					raise HostError( "invalid_operation", "graph.connect_many requires a non-empty sources array" )
				sources = [ self.__resolve( sourceID, aliases ) for sourceID in sourceIDs ]
				for source in sources :
					self.__checkMutableSubject( source, aliases, deleted )
					if not isinstance( source, Gaffer.Plug ) or not array.elementPrototype().acceptsInput( source ) :
						raise HostError( "invalid_connection", "An ArrayPlug element does not accept one of the sources" )
				startIndex = operation.get( "start_index", 0 )
				if not isinstance( startIndex, int ) or isinstance( startIndex, bool ) or startIndex < 0 :
					raise HostError( "invalid_operation", "graph.connect_many start_index must be a non-negative integer" )
				requiredSize = startIndex + len( sources )
				if requiredSize > array.maxSize() :
					raise HostError( "invalid_operation", "graph.connect_many exceeds the ArrayPlug maximum size" )
				plans.append( { "kind" : "connect_many", "array" : array, "sources" : sources, "start_index" : startIndex } )

			elif kind in ( "graph.delete", "delete" ) :
				component = self.__resolve( operation.get( "target" ), aliases )
				self.__checkMutableSubject( component, aliases, deleted )
				if not isinstance( component, Gaffer.Node ) or component.isSame( self.script ) or self.__readOnly( component ) :
					raise HostError( "read_only", "Only editable child nodes can be deleted" )
				if self.__planned( component, aliases ) :
					raise HostError( "invalid_operation_order", "A node cannot be created and deleted in one intent" )
				plans.append( { "kind" : "delete", "component" : component, "parent" : component.parent() } )
				deleted.append( component )

			elif kind in ( "node.create", "create_node" ) :
				recipeID = operation.get( "recipe", operation.get( "type" ) )
				if not isinstance( recipeID, str ) or not recipeID :
					raise HostError( "invalid_operation", "node.create requires a type or recipe string" )
				try :
					node, recipe = self.recipes.create( recipeID, operation.get( "variant" ) )
				except KeyError as error :
					raise HostError(
						"unsupported_node_recipe",
						str( error ),
						{ "hint" : "Query context with recipe_match to discover native NodeMenu recipes" },
					)
				if not isinstance( node, Gaffer.Node ) :
					raise HostError( "type_mismatch", recipeID + " did not create a Gaffer Node" )
				parent = self.__resolve( operation.get( "parent", self.__ref( self.script ) ), aliases )
				self.__checkMutableSubject( parent, aliases, deleted )
				if not isinstance( parent, Gaffer.Node ) or not parent.acceptsChild( node ) or not node.acceptsParent( parent ) or self.__readOnly( parent ) or Gaffer.MetadataAlgo.getChildNodesAreReadOnly( parent ) :
					raise HostError( "read_only", "The requested parent cannot accept this node" )
				requestedName = operation.get( "name", node.getName() )
				if not isinstance( requestedName, str ) or not requestedName :
					raise HostError( "invalid_operation", "node.create name must be a non-empty string" )
				node.setName( requestedName )
				alias = operation.get( "bind" )
				if alias :
					if not isinstance( alias, str ) or re.fullmatch( r"[A-Za-z_][A-Za-z0-9_]*", alias ) is None :
						raise HostError( "invalid_operation", "Intent aliases must be simple identifiers" )
					if alias in aliases :
						raise HostError( "invalid_operation", "Duplicate intent alias: " + alias )
					aliases[alias] = node
				plans.append( { "kind" : "create", "node" : node, "parent" : parent, "recipe" : recipe } )

			elif kind == "output.define" :
				node = self.__resolve( operation.get( "target" ), aliases )
				self.__checkMutableSubject( node, aliases, deleted )
				if not isinstance( node, GafferScene.Outputs ) :
					raise HostError( "type_mismatch", "output.define target must be a GafferScene Outputs node" )
				if self.__readOnly( node ) or self.__readOnly( node["outputs"] ) :
					raise HostError( "read_only", "The Outputs node cannot be edited" )
				name = operation.get( "name" )
				fileName = operation.get( "file_name" )
				driver = operation.get( "driver", "exr" )
				channels = operation.get( "channels", "rgba" )
				if any( not isinstance( value, str ) or not value for value in ( name, fileName, driver, channels ) ) :
					raise HostError( "invalid_operation", "output.define requires non-empty name, file_name, driver, and channels" )
				if any( child["name"].getValue() == name for child in node["outputs"].children() ) or any(
					plan["kind"] == "define_output" and plan["node"].isSame( node ) and plan["name"] == name for plan in plans
				) :
					raise HostError( "duplicate_output", "An output with this name already exists" )
				parameters = operation.get( "parameters", {} )
				if not isinstance( parameters, dict ) or any( not isinstance( key, str ) or not key for key in parameters ) :
					raise HostError( "invalid_operation", "output.define parameters must be an object with non-empty string keys" )
				compoundParameters = IECore.CompoundData( { key : _parameterData( value ) for key, value in parameters.items() } )
				fileName = fileName.replace( "\\", "/" )
				output = IECoreScene.Output( fileName, driver, channels, compoundParameters )
				plans.append( { "kind" : "define_output", "node" : node, "name" : name, "output" : output, "plug" : None } )

			elif kind == "render.start" :
				node = self.__resolve( operation.get( "target" ), aliases )
				if not isinstance( node, GafferScene.Render ) :
					raise HostError( "type_mismatch", "render.start target must be a GafferScene Render node" )
				if node["in"].getInput() is None :
					raise HostError( "render_not_ready", "The Render node has no input scene" )
				frameRange = operation.get( "frame_range" )
				if frameRange is not None and ( not isinstance( frameRange, str ) or not frameRange ) :
					raise HostError( "invalid_operation", "render.start frame_range must be a non-empty frame-list string" )
				try :
					frames = IECore.FrameList.parse( frameRange ).asList() if frameRange is not None else [ self.script.context().getFrame() ]
				except Exception as error :
					raise HostError( "invalid_operation", "Invalid render frame_range: " + str( error ) )
				if not frames or len( frames ) > 1000 :
					raise HostError( "invalid_operation", "render.start supports between 1 and 1000 frames" )
				outputs = self.__renderOutputs( node, frames )
				if not outputs :
					raise HostError( "render_not_ready", "The input scene has no file outputs" )
				name = operation.get( "name", node.getName() )
				if not isinstance( name, str ) or not name :
					raise HostError( "invalid_operation", "render.start name must be a non-empty string" )
				plans.append( { "kind" : "render_start", "node" : node, "frame_range" : frameRange, "frames" : frames, "outputs" : outputs, "name" : name } )

			elif kind == "render.cancel" :
				jobID = operation.get( "target" )
				if not isinstance( jobID, str ) or not jobID :
					raise HostError( "invalid_operation", "render.cancel target must be a render job id" )
				job = self.__renderJobs.get( jobID )
				if job is None or job.sessionID != sessionID :
					raise HostError( "unknown_job", "Unknown render job for this session" )
				plans.append( { "kind" : "render_cancel", "job" : job } )

			elif kind in ( "ui.view", "view" ) :
				node = self.__resolve( operation.get( "target" ), aliases )
				self.__checkMutableSubject( node, aliases, deleted )
				if not isinstance( node, Gaffer.Node ) or isinstance( node, Gaffer.ScriptNode ) :
					raise HostError( "type_mismatch", "ui.view target must be a non-script Node" )
				renderer = operation.get( "renderer" )
				if renderer is not None and ( not isinstance( renderer, str ) or not renderer ) :
					raise HostError( "invalid_operation", "ui.view renderer must be a non-empty string" )
				parameters = operation.get( "parameters", {} )
				if not isinstance( parameters, dict ) :
					raise HostError( "invalid_operation", "ui.view parameters must be an object" )
				unsupportedParameters = set( parameters ) - { "device" }
				if unsupportedParameters :
					raise HostError( "invalid_operation", "Unsupported ui.view parameters: " + ", ".join( sorted( unsupportedParameters ) ) )
				device = parameters.get( "device" )
				if device is not None and ( renderer != "Cycles" or not isinstance( device, str ) or not device ) :
					raise HostError( "invalid_operation", "ui.view device requires renderer Cycles and a non-empty string" )
				plans.append( { "kind" : "view", "node" : node, "renderer" : renderer, "device" : device } )

			else :
				raise HostError( "unsupported_operation", "Unsupported Gaffer operation: " + kind )

		return plans, aliases

	def __renderOutputs( self, render, frames ) :

		outputs = []
		seen = set()
		for frame in frames :
			context = Gaffer.Context( self.script.context() )
			context.setFrame( frame )
			with context :
				renderer = render["renderer"].getValue()
				if not renderer :
					raise HostError( "render_not_ready", "The Render node has no explicit renderer" )
				globals = render["in"]["globals"].getValue()
				for key, output in globals.items() :
					if not str( key ).startswith( "output:" ) or not isinstance( output, IECoreScene.Output ) :
						continue
					driver = output.getType()
					if driver.lower() in ( "iedisplay", "display" ) :
						continue
					path = context.substitute( output.getName() ).replace( "\\", "/" )
					if not path :
						continue
					path = pathlib.Path( path ).resolve().as_posix()
					identity = ( str( key ), path )
					if identity in seen :
						continue
					seen.add( identity )
					outputs.append( {
						"name" : str( key )[7:],
						"path" : path,
						"driver" : driver,
						"channels" : output.getData(),
						"frame" : frame,
					} )
		return outputs

	def __executeRenderPlan( self, plan, sessionID ) :

		if plan["kind"] == "render_cancel" :
			plan["job"].cancel()
			return plan["job"].card()

		jobID = "job:" + secrets.token_urlsafe( 18 )
		jobsDirectory = pathlib.Path( tempfile.gettempdir() ) / "opencode" / "gaffer-jobs" / self.epoch
		jobsDirectory.mkdir( parents = True, exist_ok = True )
		jobPool = GafferDispatch.LocalDispatcher.JobPool()
		dispatcher = GafferDispatch.LocalDispatcher( jobPool = jobPool )
		dispatcher["jobsDirectory"].setValue( jobsDirectory.as_posix() )
		dispatcher["jobName"].setValue( plan["name"] )
		dispatcher["executeInBackground"].setValue( True )
		if plan["frame_range"] is not None :
			dispatcher["framesMode"].setValue( GafferDispatch.Dispatcher.FramesMode.CustomRange )
			dispatcher["frameRange"].setValue( plan["frame_range"] )
		dispatcher["tasks"][0].setInput( plan["node"]["task"] )
		try :
			with self.script.context() :
				dispatcher["task"].execute()
		except Exception as error :
			raise HostError( "render_start_failed", str( error ) )
		localJobs = jobPool.jobs()
		if not localJobs :
			raise HostError( "render_start_failed", "LocalDispatcher did not create a render job" )
		job = _RenderJob(
			jobID,
			sessionID,
			self.revision,
			self.__ref( plan["node"] ),
			plan["node"].relativeName( self.script ),
			plan["name"],
			plan["frame_range"] or str( plan["frames"][0] ),
			plan["outputs"],
			dispatcher,
			localJobs[-1],
		)
		self.__renderJobs[jobID] = job
		terminal = [ key for key, item in self.__renderJobs.items() if not item.active() ]
		while len( terminal ) > 256 :
			del self.__renderJobs[terminal.pop( 0 )]
		return job.card()

	def __publishPlans( self, plans ) :

		for plan in plans :
			kind = plan["kind"]
			if kind == "create" :
				self.__registerTree( plan["node"] )
				self.__markComponent( plan["node"] )
				self.__markComponent( plan["parent"] )
			elif kind == "delete" :
				self.__dropTree( plan["component"] )
				self.__markComponent( plan["parent"] )
			elif kind == "define_output" :
				self.__registerTree( plan["plug"] )
				self.__markComponent( plan["node"] )
			elif kind == "set_value" :
				self.__markComponent( plan["plug"] )
			elif kind == "rename" :
				self.__markComponent( plan["component"] )
			elif kind == "connect" :
				self.__markComponent( plan["destination"] )
			elif kind == "connect_many" :
				self.__registerTree( plan["array"] )
				self.__markComponent( plan["array"] )

	def __coerceValue( self, plug, value ) :

		try :
			with self.script.context() :
				current = plug.getValue()
		except Exception as error :
			raise HostError( "value_unavailable", str( error ) )
		if isinstance( current, bool ) :
			if not isinstance( value, bool ) :
				raise HostError( "type_mismatch", "BoolPlug values must be JSON booleans" )
			return value
		if isinstance( current, int ) :
			if not isinstance( value, int ) or isinstance( value, bool ) :
				raise HostError( "type_mismatch", "Integer plug values must be JSON integers" )
			return value
		if isinstance( current, float ) :
			if not isinstance( value, ( int, float ) ) or isinstance( value, bool ) or not math.isfinite( value ) :
				raise HostError( "type_mismatch", "Float plug values must be finite JSON numbers" )
			return float( value )
		if isinstance( current, str ) :
			if not isinstance( value, str ) :
				raise HostError( "type_mismatch", "StringPlug values must be JSON strings" )
			return value
		if isinstance( current, ( IECore.StringVectorData, IECore.InternedStringVectorData ) ) :
			if not isinstance( value, list ) or any( not isinstance( item, str ) for item in value ) :
				raise HostError( "type_mismatch", "String vector plug values must contain JSON strings" )
			return type( current )( value )
		if isinstance( value, list ) :
			if any( isinstance( item, bool ) or not isinstance( item, ( int, float ) ) or not math.isfinite( item ) for item in value ) :
				raise HostError( "type_mismatch", "Vector plug values must contain finite JSON numbers" )
			try :
				return type( current )( *value )
			except Exception :
				try :
					return type( current )( value )
				except Exception :
					pass
		raise HostError( "unsupported_value_type", "The host cannot safely coerce a value for " + plug.typeName() )

	def __executePlan( self, plan ) :

		kind = plan["kind"]
		if kind == "set_value" :
			plan["plug"].setValue( plan["value"] )
		elif kind == "rename" :
			plan["component"].setName( plan["name"] )
		elif kind == "connect" :
			plan["destination"].setInput( plan["source"] )
		elif kind == "connect_many" :
			requiredSize = plan["start_index"] + len( plan["sources"] )
			if len( plan["array"] ) < requiredSize :
				plan["array"].resize( requiredSize )
			for index, source in enumerate( plan["sources"], start = plan["start_index"] ) :
				plan["array"][index].setInput( source )
		elif kind == "delete" :
			plan["parent"].removeChild( plan["component"] )
		elif kind == "create" :
			with self.script.context() :
				Gaffer.NodeAlgo.applyUserDefaults( plan["node"] )
			for plugName, value in plan["recipe"]["plug_values"].items() :
				plug = plan["node"].descendant( plugName )
				if plug is None or not isinstance( plug, Gaffer.ValuePlug ) :
					raise HostError( "invalid_node_recipe", "Recipe plug does not exist: " + plugName )
				plug.setValue( value )
			plan["parent"].addChild( plan["node"] )
			postCreator = plan["recipe"]["post_creator"]
			if postCreator is not None :
				with self.script.context() :
					postCreator( plan["node"], None )
		elif kind == "define_output" :
			plan["plug"] = plan["node"].addOutput( plan["name"], plan["output"] )
		elif kind == "view" :
			self.__viewNode( plan )

	def __viewNode( self, plan ) :

		node = plan["node"]
		def focusNode() :
			self.script.selection().clear()
			self.script.selection().add( node )
			self.script.setFocus( node )

		scriptWindow = GafferUI.ScriptWindow.acquire( self.script, createIfNecessary = False )
		if scriptWindow is None :
			if plan["renderer"] is not None :
				raise HostError( "viewer_unavailable", "No ScriptWindow is available for this script" )
			focusNode()
			return
		viewers = scriptWindow.getLayout().editors( GafferUI.Viewer )
		if not viewers :
			if plan["renderer"] is not None :
				raise HostError( "viewer_unavailable", "The current layout has no Viewer" )
			focusNode()
			return

		view = None
		for viewer in viewers :
			viewer.setNodeSet( Gaffer.StandardSet( [ node ] ) )
			if viewer.view() is not None :
				view = viewer.view()
				break
		plan["view"] = view
		if plan["renderer"] is None :
			focusNode()
			return
		try :
			import GafferSceneUI
		except ImportError :
			raise HostError( "viewer_unavailable", "GafferSceneUI is not available" )
		if not isinstance( view, GafferSceneUI.SceneView ) :
			raise HostError( "viewer_unavailable", "The viewed node does not provide a SceneView" )
		if plan["renderer"] not in GafferSceneUI.SceneView.registeredRenderers() :
			raise HostError(
				"unsupported_renderer",
				"SceneView renderer is not registered: " + plan["renderer"],
				{ "registered_renderers" : list( GafferSceneUI.SceneView.registeredRenderers() ) },
			)
		if plan["device"] is not None :
			try :
				import GafferCycles
			except ImportError :
				raise HostError( "unsupported_renderer", "GafferCycles is not available" )
			matchingDevices = [
				device for device in GafferCycles.devices.values()
				if device["type"].value == "OPTIX"
			]
			if plan["device"] == "OPTIX:*" and not matchingDevices :
				raise HostError( "unsupported_device", "No OptiX device is available" )
			devicePlug = view.descendant( "renderer.cycles.device" )
			if not isinstance( devicePlug, Gaffer.StringPlug ) :
				raise HostError( "viewer_unavailable", "The Cycles viewport has no device setting" )
			devicePlug.setValue( plan["device"] )
			plan["device_plug"] = devicePlug
			plan["available_devices"] = [ device["description"].value for device in matchingDevices ]
		view["renderer"]["name"].setValue( plan["renderer"] )
		focusNode()

	def __verify( self, expectations, aliases ) :

		results = []
		for expectation in expectations or [] :
			if not isinstance( expectation, dict ) :
				raise HostError( "invalid_expectation", "Each expectation must be an object" )
			predicate = expectation.get( "predicate" )
			expected = expectation.get( "value" )
			if predicate == "exists" :
				try :
					subject = self.__resolve( expectation.get( "subject" ), aliases )
					observed = self.__belongsToScript( subject )
				except HostError as error :
					if error.code != "unknown_ref" :
						raise
					observed = False
			elif predicate == "name_equals" :
				subject = self.__resolve( expectation.get( "subject" ), aliases )
				observed = subject.getName()
			elif predicate == "value_equals" :
				subject = self.__resolve( expectation.get( "subject" ), aliases )
				if not isinstance( subject, Gaffer.ValuePlug ) :
					raise HostError( "type_mismatch", "value_equals requires a ValuePlug subject" )
				with self.script.context() :
					observed = _jsonValue( subject.getValue() )
			elif predicate == "connected_to" :
				subject = self.__resolve( expectation.get( "subject" ), aliases )
				if not isinstance( subject, Gaffer.Plug ) :
					raise HostError( "type_mismatch", "connected_to requires a Plug subject" )
				observedPlug = subject.getInput()
				if expected is None :
					observed = self.__ref( observedPlug ) if observedPlug is not None else None
				else :
					expectedPlug = self.__resolve( expected, aliases )
					observed = observedPlug is not None and observedPlug.isSame( expectedPlug )
					expected = True
			else :
				raise HostError( "unsupported_expectation", "Unsupported verification predicate: " + str( predicate ) )

			ok = observed is True if predicate == "exists" and expected is None else _valuesEqual( observed, expected )
			if not ok :
				raise HostError(
					"verification_failed",
					"Postcondition failed: " + str( predicate ),
					{ "observed" : observed, "expected" : expected },
				)
			results.append( { "predicate" : predicate, "ok" : True, "observed" : observed } )
		return results

	def __preverify( self, expectations, plans, aliases ) :

		for expectation in expectations or [] :
			if not isinstance( expectation, dict ) :
				raise HostError( "invalid_expectation", "Each expectation must be an object" )
			predicate = expectation.get( "predicate" )
			expected = expectation.get( "value" )
			try :
				subject = self.__resolve( expectation.get( "subject" ), aliases )
			except HostError as error :
				if predicate == "exists" and error.code == "unknown_ref" :
					subject = None
				else :
					raise

			predicted = None
			if predicate == "name_equals" :
				for plan in reversed( plans ) :
					if plan["kind"] == "rename" and plan["component"].isSame( subject ) :
						if expected != plan["name"] :
							predicted = plan["name"]
						break
			elif predicate == "value_equals" :
				for plan in reversed( plans ) :
					if plan["kind"] == "set_value" and plan["plug"].isSame( subject ) :
						value = _jsonValue( plan["value"] )
						if not _valuesEqual( value, expected ) :
							predicted = value
						break
			elif predicate == "connected_to" :
				for plan in reversed( plans ) :
					if plan["kind"] == "connect" and plan["destination"].isSame( subject ) :
						expectedPlug = self.__resolve( expected, aliases ) if expected is not None else None
						if expectedPlug is None or not expectedPlug.isSame( plan["source"] ) :
							predicted = self.__ref( plan["source"] )
						break
			elif predicate == "exists" :
				wanted = True if expected is None else expected
				if subject is None :
					observed = False
				else :
					observed = self.__belongsToScript( subject ) or any(
						plan["kind"] == "create" and plan["node"].isSame( subject ) for plan in plans
					)
					if any( plan["kind"] == "delete" and plan["component"].isSame( subject ) for plan in plans ) :
						observed = False
				if observed != wanted :
					predicted = observed

			if predicted is not None :
				raise HostError(
					"verification_failed",
					"Postcondition contradicts the requested operations: " + str( predicate ),
					{ "predicted" : predicted, "expected" : expected },
				)

	def __verifyPlans( self, plans ) :

		results = []
		for index, plan in enumerate( plans ) :
			kind = plan["kind"]
			later = plans[index + 1:]
			if kind == "set_value" :
				if any(
					item["kind"] == "set_value" and item["plug"].isSame( plan["plug"] ) or
					item["kind"] == "connect" and item["destination"].isSame( plan["plug"] )
					for item in later
				) :
					continue
				with self.script.context() :
					observed = _jsonValue( plan["plug"].getValue() )
				expected = _jsonValue( plan["value"] )
				ok = _valuesEqual( observed, expected )
			elif kind == "rename" :
				if any( item["kind"] == "rename" and item["component"].isSame( plan["component"] ) for item in later ) :
					continue
				observed = plan["component"].getName()
				expected = plan["name"]
				ok = observed == expected
			elif kind == "connect" :
				if any( item["kind"] == "connect" and item["destination"].isSame( plan["destination"] ) for item in later ) :
					continue
				observed = plan["destination"].getInput()
				expected = plan["source"]
				ok = observed is not None and observed.isSame( expected )
			elif kind == "connect_many" :
				observed = [ plan["array"][index].getInput() for index in range( plan["start_index"], plan["start_index"] + len( plan["sources"] ) ) ]
				expected = plan["sources"]
				ok = all( item is not None and item.isSame( source ) for item, source in zip( observed, expected ) )
			elif kind == "delete" :
				observed = self.__belongsToScript( plan["component"] )
				expected = False
				ok = not observed
			elif kind == "view" :
				observed = self.script.getFocus()
				expected = plan["node"]
				ok = observed is not None and observed.isSame( expected )
				if ok and plan["renderer"] is not None :
					view = plan.get( "view" )
					ok = view is not None and view["renderer"]["name"].getValue() == plan["renderer"]
				if ok and plan["device"] is not None :
					ok = plan.get( "device_plug" ) is not None and plan["device_plug"].getValue() == plan["device"]
			elif kind == "define_output" :
				with self.script.context() :
					observed = plan["node"]["out"]["globals"].getValue().get( "output:" + plan["name"] )
				expected = plan["output"]
				ok = observed == expected
			else :
				observed = plan["node"].parent()
				expected = plan["parent"]
				ok = observed is not None and observed.isSame( expected )

			if not ok :
				raise HostError( "verification_failed", "Operation postcondition failed: " + kind )
			results.append( { "predicate" : kind, "ok" : True } )
		return results

	def __markComponent( self, component ) :

		node = component if isinstance( component, Gaffer.Node ) else component.ancestor( Gaffer.Node )
		if node is not None and self.__belongsToScript( node ) and not node.isSame( self.script ) :
			self.__dirtyNodes.add( self.__ref( node ) )
			self.__scheduleFlush()
		elif node is not None and node.isSame( self.script ) :
			self.__summaryDirty = True
			self.__scheduleFlush()

	def __dropTree( self, component ) :

		for item in list( _walk( component ) )[::-1] :
			ref = self.__refsByObject.get( item )
			if ref is not None :
				self.__objectsByRef.pop( ref, None )
				self.__tombstones[ref] = item
				self.__tombstones.move_to_end( ref )
				if isinstance( item, ( Gaffer.Node, Gaffer.Plug ) ) :
					self.__removedRefs.add( ref )
				self.__cards.pop( ref, None )
		while len( self.__tombstones ) > TOMBSTONE_LIMIT :
			oldRef, oldItem = self.__tombstones.popitem( last = False )
			if self.__refsByObject.get( oldItem ) == oldRef :
				self.__refsByObject.pop( oldItem, None )

	def __childAdded( self, parent, child ) :

		if self.__trackingSuppressed :
			return
		self.__registerTree( child )
		self.__markComponent( child )
		self.__markComponent( parent )

	def __childRemoved( self, parent, child ) :

		if self.__trackingSuppressed :
			return
		self.__dropTree( child )
		self.__markComponent( parent )
		self.__scheduleFlush()

	def __nameChanged( self, component, unusedOldName ) :

		for item in _walk( component ) :
			self.__markComponent( item )

	def __childrenReordered( self, component, unusedOldIndices ) :

		self.__markComponent( component )

	def __plugChanged( self, plug ) :

		self.__markComponent( plug )

	def __selectionChanged( self, unusedSet, member ) :

		if isinstance( member, Gaffer.Node ) :
			self.__recent.append( self.__ref( member ) )

	def __focusChanged( self, unusedScript, node ) :

		if node is not None :
			self.__recent.append( self.__ref( node ) )


class Host :

	def __init__( self, application ) :

		self.application = application
		self.recipes = Recipes.Registry( application.root() )
		self.__statesByHandle = {}
		self.__handlesByScript = {}
		self.__connections = []
		scripts = application.root()["scripts"]
		for script in scripts.children( Gaffer.ScriptNode ) :
			self.__addScript( scripts, script )
		self.__connections.append( scripts.childAddedSignal().connect( self.__addScript, scoped = True ) )
		self.__connections.append( scripts.childRemovedSignal().connect( self.__removeScript, scoped = True ) )

	def scriptHandle( self, script ) :

		return self.__handlesByScript.get( script )

	def dispatch( self, method, params ) :

		if method in ( "cancel", "resume" ) :
			handle = str( params.get( "script_handle", "" ) )
			state = self.__statesByHandle.get( handle )
			if state is None :
				raise HostError( "unknown_script", "Unknown or expired script_handle" )
			sessionID = params.get( "session_id" )
			if not isinstance( sessionID, str ) or not sessionID :
				raise HostError( "invalid_request", "session_id is required" )
			state.setCancelled( sessionID, method == "cancel" )
			return { "cancelled" : method == "cancel" }

		requestCancellation = None
		if method == "apply" :
			state = self.__statesByHandle.get( str( params.get( "script_handle", "" ) ) )
			if state is None :
				raise HostError( "unknown_script", "Unknown or expired script_handle" )
			sessionID = params.get( "session_id" )
			if not isinstance( sessionID, str ) or not sessionID :
				raise HostError( "invalid_request", "session_id is required" )
			requestCancellation = state.cancellationState( sessionID )

		def execute() :
			handle = str( params.get( "script_handle", "" ) )
			state = self.__statesByHandle.get( handle )
			if state is None :
				raise HostError( "unknown_script", "Unknown or expired script_handle" )
			if method == "hello" :
				return state.hello()
			if method == "context" :
				return state.context( params )
			if method == "sync" :
				return state.sync( params.get( "after_revision", 0 ) )
			if method == "apply" :
				return state.apply( params, requestCancellation[0], requestCancellation[1] )
			raise HostError( "unknown_method", "Unknown Gaffer host method: " + method )

		def executeSafely() :
			try :
				return True, execute()
			except Exception as error :
				return False, error

		succeeded, result = GafferUI.EventLoop.executeOnUIThread( executeSafely, waitForResult = True )
		if not succeeded :
			raise result
		return result

	def __addScript( self, unusedParent, script ) :

		if not isinstance( script, Gaffer.ScriptNode ) or script in self.__handlesByScript :
			return
		state = ScriptState( script, self.recipes )
		self.__handlesByScript[script] = state.handle
		self.__statesByHandle[state.handle] = state

	def __removeScript( self, unusedParent, script ) :

		handle = self.__handlesByScript.pop( script, None )
		state = self.__statesByHandle.pop( handle, None ) if handle is not None else None
		if state is not None :
			state.close()


class HostServer :

	def __init__( self, host ) :

		self.host = host
		self.token = secrets.token_hex( 32 )
		self.socket = socket.socket( socket.AF_INET, socket.SOCK_STREAM )
		self.socket.setsockopt( socket.SOL_SOCKET, socket.SO_REUSEADDR, 1 )
		self.socket.bind( ( "127.0.0.1", 0 ) )
		self.socket.listen( 16 )
		self.port = self.socket.getsockname()[1]
		self.running = True
		self.thread = threading.Thread( target = self.__serve, name = "GafferOpenCodeHost", daemon = True )
		self.thread.start()

	def info( self ) :

		return { "host" : "127.0.0.1", "port" : self.port, "token" : self.token, "protocol" : PROTOCOL_VERSION }

	def close( self ) :

		self.running = False
		try :
			self.socket.close()
		except OSError :
			pass

	def __serve( self ) :

		while self.running :
			try :
				client, unusedAddress = self.socket.accept()
			except OSError :
				break
			threading.Thread( target = self.__handleClient, args = ( client, ), daemon = True ).start()

	def __handleClient( self, client ) :

		requestID = None
		try :
			client.settimeout( 2 )
			buffer = b""
			while b"\n" not in buffer :
				chunk = client.recv( 65536 )
				if not chunk :
					break
				buffer += chunk
				if len( buffer ) > MAX_REQUEST_BYTES :
					raise HostError( "request_too_large", "Gaffer host request exceeded the size limit" )
			request = json.loads( buffer.split( b"\n", 1 )[0].decode( "utf-8" ) )
			requestID = request.get( "id" )
			if not secrets.compare_digest( str( request.get( "token", "" ) ), self.token ) :
				raise HostError( "unauthorized", "Invalid Gaffer host token" )
			result = self.host.dispatch( str( request.get( "method", "" ) ), request.get( "params" ) or {} )
			response = { "id" : requestID, "ok" : True, "result" : result }
		except HostError as error :
			code = {
				"unknown_script" : "stale_handle", "revision_conflict" : "stale_revision",
				"unknown_method" : "method_not_found", "script_quarantined" : "quarantined",
				"rollback_failed" : "quarantined", "request_too_large" : "invalid_request",
				"read_only" : "permission_denied",
			}.get( error.code, error.code )
			if code.startswith( ( "invalid_", "unsupported_" ) ) and code != "invalid_request" :
				code = "invalid_args"
			if code not in ( "unauthorized", "invalid_request", "method_not_found", "method_disabled", "invalid_args", "stale_handle", "stale_revision", "expectation_failed", "mode_conflict", "busy", "quarantined", "permission_denied", "internal" ) :
				code = "internal"
			response = { "id" : requestID, "ok" : False, "error" : { "code" : code, "message" : str( error )[:300] } }
		except Exception as error :
			response = { "id" : requestID, "ok" : False, "error" : { "code" : "internal", "message" : str( error )[:300] } }

		try :
			client.sendall( ( json.dumps( response, allow_nan = False ) + "\n" ).encode( "utf-8" ) )
		finally :
			client.close()
