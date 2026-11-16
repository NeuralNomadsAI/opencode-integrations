"""OpenCode Link for Gaffer: the installed part.

Started with Gaffer's interface by startup/gui/openCode.py (initialise): it listens on 127.0.0.1 and offers each
open script to the OpenCode services every 5 seconds, so any conversation (CodeNomad, the terminal, the OpenCode
panel) can attach it. OPENCODE_GAFFER_OFFER=0 in Gaffer's environment keeps it from offering them. A background
`gaffer python` (verification, renders: OPENCODE_GAFFER_ROLE) never starts it.
"""

import atexit
import os

from .Host import Host, HostError, HostServer, ScriptState


__host = None
__server = None
__advertiser = None
__panelOwners = set()


def _snapshot() :

	host, server = __host, __server
	if host is None or server is None or not server.running :
		return None, None, []
	return server.port, server.token, host.offers()


def initialise( application = None, advertise = None, root = None, ui = True ) :

	"""Starts the host once per Gaffer process; with advertise (by default unless OPENCODE_GAFFER_OFFER=0 or a
	background role), offers its scripts to OpenCode."""

	global __host, __server, __advertiser
	if __host is not None :
		return
	host = Host( application, root = root, ui = ui )
	server = HostServer( host )
	__host = host
	__server = server
	if advertise is None :
		advertise = os.environ.get( "OPENCODE_GAFFER_OFFER", "1" ) != "0" and not os.environ.get( "OPENCODE_GAFFER_ROLE" )
	if advertise :
		from .Advertiser import Advertiser

		__advertiser = Advertiser( _snapshot ).start()


def pump( seconds = 0.05, stop = None ) :

	"""`gaffer python`: serve the queued requests on the main thread (the interface serves them by itself)."""

	if __host is not None :
		__host.ui.pump( seconds, stop )


def shutdown() :

	global __host, __server, __advertiser
	advertiser, server = __advertiser, __server
	token = server.token if server is not None else None
	if advertiser is not None :
		advertiser.stop( token )
	if server is not None :
		server.close()
	if __host is not None :
		__host.close()
	__advertiser = None
	__server = None
	__host = None
	__panelOwners.clear()


atexit.register( shutdown )


def acquirePanel( channel ) :

	"""A panel uses the host (started with Gaffer's interface)."""

	__panelOwners.add( channel )


def releasePanel( channel ) :

	"""A panel closed: the host and its offers stay, for the other conversations attached to this Gaffer."""

	__panelOwners.discard( channel )


def hostInfo() :

	if __server is None :
		raise RuntimeError( "OpenCode Link for Gaffer has not been initialised" )
	return __server.info()


def offerInfo( script ) :

	"""What a panel needs to find this script's offer among the offered hosts."""

	if __host is None :
		raise RuntimeError( "OpenCode Link for Gaffer has not been initialised" )
	label, key = __host.offerOf( script ) or ( None, None )
	return { "label" : label, "offer_key" : key, "advertising" : __advertiser is not None }


def scriptHandle( script ) :

	if __host is None :
		raise RuntimeError( "OpenCode Link for Gaffer has not been initialised" )
	return __host.scriptHandle( script )


def currentHost() :

	return __host


__all__ = [
	"Host", "HostError", "HostServer", "ScriptState", "acquirePanel", "currentHost", "hostInfo", "initialise",
	"offerInfo", "pump", "releasePanel", "scriptHandle", "shutdown",
]
