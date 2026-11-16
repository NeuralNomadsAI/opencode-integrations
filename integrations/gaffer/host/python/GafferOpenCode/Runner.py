"""execute_code and release: Python run in Gaffer on the attached script, grouped as one undo step, undone when it fails.

A run that changes the script is one Gaffer.UndoScope. When it raises after a change, the step is undone and the
script compared with its serialisation before the run: restored, or the script is quarantined (changes refused,
reads and read_only runs allowed) until the agent read it and releases it with that read's revision. read_only runs
(the plugin's fixed programs, inspections) make no undo step: a change they make is reported. Hints (Hints.py) are
computed in the except block, before anything is undone.
"""

import contextlib
import hashlib
import io
import json
import os
import traceback

STDOUT_MAX = 64 * 1024
TRACE_MAX = 16 * 1024
FILENAME_MAX = 1024
UNDO_STEP = "OpenCode: code"
QUARANTINED = (
	"A failed change could not be proven undone: changes are refused until you read the script (gaffer_context) "
	"and release the quarantine with that read's revision (gaffer_context release_quarantine)."
)


class _Capture( io.StringIO ) :

	"""stdout or stderr of one run, bounded."""

	def write( self, text ) :

		if self.tell() < STDOUT_MAX * 2 :
			return io.StringIO.write( self, text )
		return len( text )


def fingerprint( script ) :

	"""The script as Gaffer serialises it: every node, plug value and connection an undone change must give back."""

	try :
		return hashlib.sha256( script.serialise().encode( "utf-8" ) ).hexdigest()
	except Exception as error :
		return "unserialisable: {}".format( error )


def _jsonRepr( value ) :

	return json.loads( json.dumps( value, default = repr ) )


def _savedAt( script ) :

	try :
		path = script["fileName"].getValue()
		return os.path.getmtime( path ) if path and os.path.isfile( path ) else None
	except Exception :
		return None


def namespace( script, application ) :

	import imath
	import IECore
	import Gaffer
	import GafferDispatch
	import GafferImage
	import GafferScene

	return {
		"Gaffer" : Gaffer, "GafferScene" : GafferScene, "GafferImage" : GafferImage, "GafferDispatch" : GafferDispatch,
		"IECore" : IECore, "imath" : imath, "script" : script, "application" : application,
		"__name__" : "__opencode__", "result" : {},
	}


def executeCode( state, params, application = None, explain = None ) :

	import Gaffer
	from .Host import HostError

	code = params.get( "code" )
	if not isinstance( code, str ) or not code.strip() :
		raise HostError( "invalid_request", "code is required" )
	readOnly = params.get( "read_only" ) is True
	if state.quarantined and not readOnly :
		raise HostError( "script_quarantined", QUARANTINED, { "quarantine" : state.quarantine or {} } )
	filename = params.get( "filename" )
	if filename is not None and (
		not isinstance( filename, str ) or not 0 < len( filename ) <= FILENAME_MAX or any( ord( c ) < 32 for c in filename )
	) :
		raise HostError( "invalid_request", "filename must be a file path of at most 1024 characters" )

	script = state.script
	scope = namespace( script, application )
	if filename :
		scope["__file__"] = filename
	state.flush()
	revisionBefore = state.revision
	written = _savedAt( script )
	before = None if readOnly else fingerprint( script )
	actions = [ 0 ]

	def counted( unusedScript, unusedAction, stage ) :
		if stage == Gaffer.Action.Stage.Do :
			actions[0] += 1

	connection = script.actionSignal().connect( counted, scoped = True )
	out, err = _Capture(), _Capture()
	error = trace = hint = None
	group = contextlib.nullcontext() if readOnly else Gaffer.UndoScope( script, mergeGroup = "" )
	try :
		with group :
			try :
				with contextlib.redirect_stdout( out ), contextlib.redirect_stderr( err ) :
					exec( compile( code, filename or "<opencode execute_code>", "exec" ), scope )
			except BaseException as exc :  # SystemExit from supplied code must not quit Gaffer
				error = "{}: {}".format( type( exc ).__name__, exc )[:2000]
				trace = traceback.format_exc()[-TRACE_MAX:]
				if explain is not None :
					try :
						hint = explain( exc, code, filename, script, scope )
					except Exception :  # a hint never hides the error
						hint = None
	finally :
		del connection

	saved = ( lambda now : now is not None and now != written )( _savedAt( script ) )
	changed = actions[0] > 0 or state.hasPendingChanges() or saved
	restored = None
	if error is not None and actions[0] and not readOnly :
		rollback = None
		try :
			if not script.undoAvailable() :
				raise RuntimeError( "the run left no undo item to undo" )
			script.undo()
			state.discardRedo()
		except Exception as undoError :
			rollback = undoError
		restored = rollback is None and before == fingerprint( script )
		if not restored :
			state.quarantined = True
			state.quarantine = {
				"cause" : ( "failed code: " + error )[:200],
				"release" : "read the script (gaffer_context), check what may have changed, then release the "
				"quarantine with that read's revision",
				**( { "undo_error" : str( rollback )[:300] } if rollback is not None else {} ),
			}
		state.markSummary()
		state.flush( cause = "rollback" )
	else :
		state.flush( cause = "opencode" )

	output = {
		"status" : "failed" if error else "executed",
		"script_handle" : state.handle,
		"result" : _jsonRepr( scope.get( "result", {} ) ),
		"stdout" : out.getvalue()[:STDOUT_MAX],
		"stderr" : err.getvalue()[:STDOUT_MAX],
		"truncated" : len( out.getvalue() ) > STDOUT_MAX or len( err.getvalue() ) > STDOUT_MAX,
		"revision_before" : revisionBefore,
		"revision_after" : state.revision,
		"changed" : changed,
	}
	if changed and not readOnly and error is None and actions[0] :
		output["undo_step"] = UNDO_STEP
	if saved :
		output["saved"] = True
	if readOnly and actions[0] :
		output["warnings"] = [ "OpenCode: this read_only run changed the script; no undo step holds it." ]
	if error :
		output.update( message = error, traceback = trace )
		if hint :
			output["hint"] = hint
		if restored is not None :
			output["restored"] = restored
			if not restored :
				output["quarantine"] = state.quarantine
	return output


def release( state, params ) :

	from .Host import HostError

	if not state.quarantined :
		return { "released" : False, "revision" : state.revision }
	revision = params.get( "revision" )
	state.flush()
	if not isinstance( revision, int ) or isinstance( revision, bool ) or revision != state.revision :
		raise HostError(
			"revision_conflict",
			"Read the script again (gaffer_context) and release with its revision.",
			{ "current_revision" : state.revision },
		)
	state.quarantined = False
	state.quarantine = None
	return { "released" : True, "revision" : state.revision }
