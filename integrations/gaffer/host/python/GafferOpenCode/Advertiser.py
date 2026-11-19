"""Offers this Gaffer's open scripts to the discovered OpenCode services (standard library only).

Runs on its own thread: discovery and RPC calls never block Gaffer's main thread. One offer per open script
(labelled with its file name), renewed every 5 seconds straight at the services that took it; a script closed is
withdrawn, and an offer whose renewals stop expires within 15 seconds anyway. Results carry service file names,
labels and outcomes only, never the daemon password nor the host token.
"""

import os
import re
import secrets
import threading
import time

RPC_ID = "neuralnomads.gaffer"
PROTOCOL = 1
RENEW_SECONDS = 5.0
# One renewal may wait this long for a busy service: an offer lasts 15 s, renewed every 5 s.
RENEW_TIMEOUT = 8.0
# Renewals go straight to the services that took the offers; every this many renewals (or after a failure) the
# services are discovered again, which finds a service started since.
DISCOVER_EVERY = 12


def _launchKey() :

	"""A Gaffer started by OpenCode offers its first script under the key OpenCode chose."""

	key = os.environ.get( "OPENCODE_GAFFER_OFFER_KEY", "" )
	return key if re.fullmatch( r"gaffer-launch-[0-9a-f]{32}", key ) else None


# Stable for the life of the Gaffer process; each script's key adds its number (the first script: none).
OFFER_KEY = _launchKey() or "gaffer-{}-{}".format( os.getpid(), secrets.token_hex( 8 ) )


def offerKey( number ) :

	return OFFER_KEY if number == 0 else "{}.{}".format( OFFER_KEY, number )


class Advertiser :

	"""`snapshot()` returns ( port, token, [ ( script_handle, label, offer_key ), ... ] ), or a None token while not
	ready."""

	def __init__( self, snapshot, env = None, interval = RENEW_SECONDS ) :

		self.__snapshot = snapshot
		self.__env = env
		self.__interval = interval
		self.__stop = threading.Event()
		self.__lock = threading.Lock()
		self.__results = []
		self.__count = 0
		self.__targets = {}
		self.__offered = {}  # offer_key: token, of the offers made
		self.__thread = threading.Thread( target = self.__loop, name = "GafferOpenCodeAdvertiser", daemon = True )

	def start( self ) :

		self.__thread.start()
		return self

	@property
	def results( self ) :

		with self.__lock :
			return list( self.__results )

	def advertiseOnce( self ) :

		port, token, scripts = self.__snapshot()
		if token is None :
			return None
		offers = [
			{
				"host" : "127.0.0.1", "port" : port, "token" : token, "script_handle" : handle, "label" : label,
				"protocol" : PROTOCOL, "offer_key" : key,
			}
			for handle, label, key in scripts
		]
		# A script closed since: its offer is withdrawn rather than left to expire.
		keys = { offer["offer_key"] for offer in offers }
		for key in [ key for key in self.__offered if key not in keys ] :
			self.__withdraw( key, self.__offered.pop( key ) )
		results = self.__renew( offers ) if offers else []
		for offer in offers :
			self.__offered[offer["offer_key"]] = token
		with self.__lock :
			self.__results = results
		return results

	def __renew( self, offers ) :

		"""Renews at the services that took the offers (one request per offer); discovers them again on a failure and
		every DISCOVER_EVERY renewals (a discovery reads each service's info and the plugin's status first)."""

		from . import opencode_service  # the kit's client, vendored into the payload

		self.__count += 1
		targets = self.__targets
		if targets and self.__count % DISCOVER_EVERY :
			results, failed = [], False
			for name, ( service, directory ) in targets.items() :
				for offer in offers :
					body = None
					try :
						status, body = opencode_service.call( service, RPC_ID, "advertise", offer, directory, RENEW_TIMEOUT )
						outcome = opencode_service._outcome( "advertise", status, body )
					except ( OSError, ValueError ) :
						outcome = "error"
					results.append( { "service" : name, "label" : offer["label"], "outcome" : outcome, **opencode_service.panel_runtime( outcome, body ) } )
					failed = failed or outcome not in ( "accepted", "renewed" )
			if not failed :
				return results
		found = {}
		results = []
		try :
			services = opencode_service.discover( env = self.__env, timeout = RENEW_TIMEOUT )
		except ( OSError, ValueError ) :
			services = []
		for service in services :
			for directory in opencode_service.location_candidates( None, self.__env ) :
				outcomes = []
				try :
					deadline = time.monotonic() + RENEW_TIMEOUT
					status, body = opencode_service._ready( service, RPC_ID, directory, deadline )
					if opencode_service._unavailable( status, body ) :
						continue
					if status != 200 :
						outcomes = [ ( offer, "error", None ) for offer in offers ]
					else :
						for offer in offers :
							try :
								status, body = opencode_service.call(
									service, RPC_ID, "advertise", offer, directory, max( 0.5, deadline - time.monotonic() )
								)
								outcomes.append( ( offer, opencode_service._outcome( "advertise", status, body ), body ) )
							except ( OSError, ValueError ) :
								outcomes.append( ( offer, "error", None ) )
				except ( OSError, ValueError ) :
					outcomes = [ ( offer, "error", None ) for offer in offers ]
				for offer, outcome, body in outcomes :
					results.append( { "service" : service.file, "label" : offer["label"], "outcome" : outcome, **opencode_service.panel_runtime( outcome, body ) } )
				if any( outcome in ( "accepted", "renewed" ) for _, outcome, _ in outcomes ) :
					found[service.file] = ( service, directory )
				break
			else :
				results.append( { "service" : service.file, "outcome" : "unavailable" } )
		self.__targets = found
		return results

	def __withdraw( self, key, token ) :

		from . import opencode_service

		try :
			opencode_service.withdraw_all( RPC_ID, key, token, env = self.__env )
		except ( OSError, ValueError ) :
			pass

	def __loop( self ) :

		while not self.__stop.is_set() :
			try :
				results = self.advertiseOnce() or []
			except Exception :  # the next renewal tries again
				results = []
			taken = all( result.get( "outcome" ) in ( "accepted", "renewed" ) for result in results ) and results
			# A renewal that failed is tried again soon: an offer lasts 15 s from the last one taken.
			self.__stop.wait( self.__interval if taken else min( self.__interval, 1.0 ) )

	def stop( self, token, withdraw = True ) :

		"""Stops renewing; withdraws every offer from every service with the token it was announced with."""

		from . import opencode_service

		self.__stop.set()
		if self.__thread.is_alive() and self.__thread is not threading.current_thread() :
			self.__thread.join( timeout = opencode_service.TIMEOUT * 2 )
		if not withdraw or token is None :
			return []
		results = []
		for key in list( self.__offered ) :
			try :
				results += opencode_service.withdraw_all( RPC_ID, key, token, env = self.__env )
			except ( OSError, ValueError ) :
				pass
		self.__offered = {}
		return results
