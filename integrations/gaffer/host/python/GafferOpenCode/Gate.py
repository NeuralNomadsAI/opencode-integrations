"""Runs requests on Gaffer's main thread, and says which runs.

In the interface, through GafferUI.EventLoop.executeOnUIThread (callable from any thread: it queues the call and
schedules it with a queued Qt invocation, without waiting for the main thread); in `gaffer python`, through pump(),
which the main thread calls. A request the main thread has not taken within QUEUE_WAIT_S is answered busy, naming
what runs there and since when. `running()` answers from any thread. Never one request inside another: a request
reached from a nested event loop (a dialog, processEvents) while another runs is put back and retried.
"""

import queue
import threading
import time

QUEUE_WAIT_S = 10.0
RUN_LIMIT_S = 3600.0
RETRY_S = 0.05


class GateError( RuntimeError ) :

	def __init__( self, code, message, data = None ) :

		RuntimeError.__init__( self, message )
		self.code = code
		self.data = data or {}


class UIThreadGate :

	def __init__( self, post = None ) :

		# post( callable ) schedules a call on the main thread (the interface's event loop); None: pump() serves.
		self.__post = post
		self.__mainThread = threading.get_ident()
		self.__running = None
		self.__queued = 0
		self.__count = threading.Lock()
		self.__jobs = queue.Queue()

	def running( self ) :

		current = self.__running
		if current is None :
			return { "busy" : False, "queued" : self.__queued }
		label, since = current
		return { "busy" : True, "running" : label, "seconds" : round( time.monotonic() - since, 1 ), "queued" : self.__queued }

	def __invoke( self, label, callback ) :

		self.__running = ( label, time.monotonic() )
		try :
			return callback()
		finally :
			self.__running = None

	def pump( self, seconds = 0.05, stop = None ) :

		"""`gaffer python`: runs the queued requests on this (the main) thread for `seconds`, or until `stop()`."""

		deadline = time.monotonic() + seconds
		while time.monotonic() < deadline and not ( stop is not None and stop() ) :
			try :
				job = self.__jobs.get( timeout = min( 0.05, max( 0.0, deadline - time.monotonic() ) ) )
			except queue.Empty :
				continue
			job()

	def __schedule( self, job ) :

		if self.__post is None :
			self.__jobs.put( job )
		else :
			self.__post( job )

	def run( self, callback, label = "a request" ) :

		if threading.get_ident() == self.__mainThread :
			return self.__invoke( label, callback )
		started = threading.Event()
		complete = threading.Event()
		expired = threading.Event()
		lock = threading.Lock()
		result = []

		def invoke() :
			if self.__running is not None :
				# Reached from a nested event loop while a request runs: later, never inside it.
				threading.Timer( RETRY_S, lambda : self.__schedule( invoke ) ).start()
				return
			with lock :
				if expired.is_set() :
					return
				started.set()
			try :
				result.append( ( True, self.__invoke( label, callback ) ) )
			except Exception as error :
				result.append( ( False, error ) )
			finally :
				complete.set()

		with self.__count :
			self.__queued += 1
		try :
			self.__schedule( invoke )
			if not started.wait( QUEUE_WAIT_S ) :
				with lock :
					if not started.is_set() :
						expired.set()
						now = self.running()
						doing = (
							"it still runs {} ({} s so far): nothing was done for this request; ask again once it has "
							"finished".format( now["running"], now["seconds"] )
							if now.get( "busy" )
							else "a modal dialog, a long computation or a script outside OpenCode holds it"
						)
						raise GateError(
							"ui_timeout",
							"Gaffer's main thread did not take the request within {:.0f} s: {}".format( QUEUE_WAIT_S, doing ),
							{ "running" : now.get( "running" ), "seconds" : now.get( "seconds" ) },
						)
		finally :
			with self.__count :
				self.__queued -= 1
		if not complete.wait( RUN_LIMIT_S ) :
			raise GateError( "ui_timeout", "Gaffer did not finish the request within {:.0f} s".format( RUN_LIMIT_S ) )
		succeeded, value = result[0]
		if not succeeded :
			raise value
		return value
