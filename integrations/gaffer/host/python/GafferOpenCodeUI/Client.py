import json
import pathlib
import time
import uuid
import weakref

from Qt import QtCore

from GafferOpenCode import panelServices


class ClientProcess :

	__instance = None

	@classmethod
	def acquire( cls ) :

		if cls.__instance is None :
			cls.__instance = cls()
		return cls.__instance

	def __init__( self ) :

		self.__process = QtCore.QProcess()
		self.__process.setProcessChannelMode( QtCore.QProcess.SeparateChannels )
		self.__process.readyReadStandardOutput.connect( self.__readStdout )
		self.__process.readyReadStandardError.connect( self.__readStderr )
		self.__process.started.connect( self.__started )
		self.__process.finished.connect( self.__finished )
		self.__process.errorOccurred.connect( self.__processError )
		self.__channels = {}
		self.__targets = {}
		self.__pending = []
		self.__stdout = b""
		self.__nextRequest = 0
		self.__problem = ""
		self.__launchAt = time.monotonic() + 5
		self.__timer = QtCore.QTimer( self.__process )
		self.__timer.timeout.connect( self.__start )
		self.__timer.start( 1000 )

	## One panel's events; the callback is held weakly.
	def register( self, callback ) :

		channel = str( uuid.uuid4() )
		try :
			callbackRef = weakref.WeakMethod( callback )
		except TypeError :
			callbackRef = weakref.ref( callback )
		self.__channels[channel] = callbackRef
		return channel

	def close( self, channel ) :

		if channel not in self.__channels :
			return
		self.send( channel, "close", {} )
		self.__channels.pop( channel, None )
		self.__targets.pop( channel, None )
		self.__pending = [item for item in self.__pending if item[0] != channel]

	def send( self, channel, method, params ) :

		if method in ( "connect", "new_session" ) :
			previous = self.__targets.get( channel, {} )
			if method == "new_session" or previous.get( "directory" ) != params.get( "directory" ) :
				self.__pending = [item for item in self.__pending if item[0] != channel]
			else :
				params = { **params, "session_id" : previous.get( "session_id" ), "draft" : previous.get( "draft", False ) }
			self.__targets[channel] = params
		if self.__process.state() == QtCore.QProcess.Running :
			self.__process.write( self.__message( channel, method, params ) )
		elif method not in ( "connect", "new_session", "close" ) :
			if method == "interrupt" :
				self.__pending = [item for item in self.__pending if item[0] != channel or item[1] != "prompt"]
			if len( self.__pending ) >= 100 :
				self.__emit( channel, "error", { "message" : "OpenCode sidecar request queue is full" } )
				return
			self.__pending.append( ( channel, method, params ) )
		self.__start()

	def __message( self, channel, method, params ) :

		self.__nextRequest += 1
		return ( json.dumps( {
			"id" : str( self.__nextRequest ),
			"channel" : channel,
			"method" : method,
			"params" : params,
		} ) + "\n" ).encode( "utf-8" )

	def __start( self ) :

		if not self.__targets or self.__process.state() != QtCore.QProcess.NotRunning :
			return
		# Built into the installed part, beside the Python packages.
		sidecar = pathlib.Path( __file__ ).resolve().parents[2] / "sidecar.cjs"
		if not sidecar.is_file() :
			self.__blocked( "OpenCode Link sidecar is missing; reinstall the application part." )
			return
		from GafferOpenCode.opencode_service import panel_launcher

		services = panelServices()
		waiting = time.monotonic() < self.__launchAt
		runtime, bun = panel_launcher( services, "NEURALNOMADS_GAFFER_NODE", wait = waiting )
		if not runtime :
			if not waiting :
				self.__blocked( "No OpenCode runtime, NEURALNOMADS_GAFFER_NODE executable, or Node on PATH." )
			return
		environment = QtCore.QProcessEnvironment.systemEnvironment()
		environment.remove( "BUN_BE_BUN" )
		if bun :
			environment.insert( "BUN_BE_BUN", "1" )
		self.__process.setProcessEnvironment( environment )
		self.__process.setProgram( runtime )
		self.__process.setArguments( [ str( sidecar ) ] )
		self.__process.setWorkingDirectory( str( sidecar.parent.parent ) )
		self.__process.start()

	def __blocked( self, message ) :

		if message != self.__problem :
			self.__problem = message
			self.__emitAll( "error", { "message" : message } )

	def __started( self ) :

		pending = self.__pending
		self.__pending = []
		self.__problem = ""
		for channel, params in self.__targets.items() :
			self.__process.write( self.__message( channel, "connect", params ) )
		for channel, method, params in pending :
			self.__process.write( self.__message( channel, method, params ) )

	def __finished( self, exitCode, unusedExitStatus ) :

		self.__stdout = b""
		self.__emitAll( "status", { "state" : "connecting" } )

	def __processError( self, unusedError ) :

		self.__emitAll( "error", { "message" : "OpenCode sidecar process error: " + self.__process.errorString() } )

	def __readStdout( self ) :

		self.__stdout += bytes( self.__process.readAllStandardOutput() )
		while b"\n" in self.__stdout :
			lineBytes, self.__stdout = self.__stdout.split( b"\n", 1 )
			line = lineBytes.decode( "utf-8", errors = "replace" )
			if not line.strip() :
				continue
			try :
				message = json.loads( line )
			except Exception :
				self.__emitAll( "error", { "message" : "Malformed sidecar output: " + line[:200] } )
				continue
			channel = message.get( "channel" )
			if channel and message.get( "event" ) :
				if message["event"] == "session" and channel in self.__targets :
					self.__targets[channel] = { **self.__targets[channel], "session_id" : message["data"]["id"], "draft" : message["data"].get( "draft", False ) }
				self.__emit( channel, message["event"], message.get( "data" ) or {} )
			elif message.get( "error" ) and not message.get( "id" ) :
				self.__emitAll( "error", message["error"] )

	def __readStderr( self ) :

		text = bytes( self.__process.readAllStandardError() ).decode( "utf-8", errors = "replace" ).strip()
		if text :
			self.__emitAll( "diagnostic", { "message" : text[-2000:] } )

	def __emit( self, channel, name, data ) :

		callbackRef = self.__channels.get( channel )
		callback = callbackRef() if callbackRef is not None else None
		if callback is None :
			self.__channels.pop( channel, None )
			return
		callback( name, data )

	def __emitAll( self, name, data ) :

		for channel in list( self.__channels.keys() ) :
			self.__emit( channel, name, data )
