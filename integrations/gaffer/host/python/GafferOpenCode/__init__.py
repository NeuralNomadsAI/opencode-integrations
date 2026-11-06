from .Host import Host, HostServer


__host = None
__server = None


def initialise( application ) :

	global __host, __server
	if __host is not None :
		return
	host = Host( application )
	server = HostServer( host )
	__host = host
	__server = server


def hostInfo() :

	if __server is None :
		raise RuntimeError( "Gaffer OpenCode host has not been initialised" )
	return __server.info()


def scriptHandle( script ) :

	if __host is None :
		raise RuntimeError( "Gaffer OpenCode host has not been initialised" )
	return __host.scriptHandle( script )
