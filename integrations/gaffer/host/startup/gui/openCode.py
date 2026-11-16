# OpenCode Link for Gaffer: starts the installed part with Gaffer's interface, so each open script is offered to
# OpenCode without any panel, and registers the OpenCode panel. Never fails Gaffer's startup.
import pathlib
import sys

extensionRoot = pathlib.Path( __file__ ).resolve().parents[2]
pythonRoot = str( extensionRoot / "python" )
if pythonRoot not in sys.path :
	sys.path.insert( 0, pythonRoot )

try :
	import GafferOpenCode
	GafferOpenCode.initialise( application )
except Exception as error :
	print( "OpenCode Link did not start: {}".format( error ) )

try :
	import GafferUI
	import GafferOpenCodeUI
	GafferUI.Layouts.acquire( application.root() ).registerEditor( "OpenCode for Gaffer" )
except Exception as error :
	print( "The OpenCode panel is unavailable: {}".format( error ) )
