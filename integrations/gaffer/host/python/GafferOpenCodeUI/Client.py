import json
import os
import pathlib
import shutil
import uuid
import weakref

from Qt import QtCore


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
		self.__connectParams = {}
		self.__pending = []
		self.__stdout = b""
		self.__nextRequest = 0
		self.__start()

	def register( self, callback, connectParams ) :

		channel = str( uuid.uuid4() )
		try :
			callbackRef = weakref.WeakMethod( callback )
		except TypeError :
			callbackRef = weakref.ref( callback )
		self.__channels[channel] = callbackRef
		self.__connectParams[channel] = connectParams
		self.send( channel, "connect", connectParams )
		return channel

	def close( self, channel ) :

		if channel not in self.__channels :
			return
		self.send( channel, "close", {} )
		self.__channels.pop( channel, None )
		self.__connectParams.pop( channel, None )

	def send( self, channel, method, params ) :

		message = self.__message( channel, method, params )
		if self.__process.state() == QtCore.QProcess.Running :
			self.__process.write( message )
		else :
			if len( self.__pending ) >= 100 :
				self.__emit( channel, "error", { "message" : "OpenCode sidecar request queue is full" } )
				return
			self.__pending.append( message )
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

		if self.__process.state() != QtCore.QProcess.NotRunning :
			return
		bun = os.environ.get( "NEURALNOMADS_GAFFER_BUN" ) or shutil.which( "bun" )
		if not bun :
			self.__pending = []
			self.__emitAll( "error", { "message" : "Install Bun or set NEURALNOMADS_GAFFER_BUN" } )
			return
		sidecar = pathlib.Path(
			os.environ.get(
				"NEURALNOMADS_GAFFER_SIDECAR",
				str( pathlib.Path( __file__ ).resolve().parents[2] / "sidecar.js" ),
			)
		)
		if not sidecar.is_file() :
			self.__pending = []
			self.__emitAll( "error", { "message" : "OpenCode Gaffer sidecar not found: " + str( sidecar ) } )
			return
		self.__process.setProgram( bun )
		self.__process.setArguments( [ str( sidecar ) ] )
		self.__process.setWorkingDirectory( str( sidecar.parent.parent ) )
		self.__process.start()

	def __started( self ) :

		pending = self.__pending
		self.__pending = []
		for message in pending :
			self.__process.write( message )

	def __finished( self, exitCode, unusedExitStatus ) :

		self.__pending = []
		self.__emitAll( "error", { "message" : "OpenCode sidecar stopped with exit code " + str( exitCode ) } )

	def __processError( self, unusedError ) :

		self.__pending = []
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
				self.__emit( channel, message["event"], message.get( "data" ) or {} )
			elif message.get( "error" ) :
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
			self.__connectParams.pop( channel, None )
			return
		callback( name, data )

	def __emitAll( self, name, data ) :

		for channel in list( self.__channels.keys() ) :
			self.__emit( channel, name, data )
