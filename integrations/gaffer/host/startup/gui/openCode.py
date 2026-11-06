import functools
import os
import pathlib
import sys

import GafferUI
from Qt import QtCore
from Qt import QtGui


extensionRoot = pathlib.Path( __file__ ).resolve().parents[2]
pythonRoot = str( extensionRoot / "python" )
if pythonRoot not in sys.path :
	sys.path.insert( 0, pythonRoot )

import GafferOpenCode

GafferOpenCode.initialise( application )

import GafferOpenCodeUI

GafferUI.Layouts.acquire( application.root() ).registerEditor( "Opencode" )


def __showLocalDocumentation( relativePath ) :

	path = pathlib.Path( os.environ["GAFFER_ROOT"], "doc", "gaffer", "html", *relativePath )
	QtGui.QDesktopServices.openUrl( QtCore.QUrl.fromLocalFile( str( path ) ) )


documentationItems = {
	"User Guide" : ( "index.html", ),
	"Node Reference" : ( "Reference", "NodeReference", "index.html" ),
	"Keyboard Shortcuts" : ( "Interface", "ControlsAndShortcuts", "index.html" ),
}
scriptWindowMenu = GafferUI.ScriptWindow.menuDefinition( application.root() )
for label, relativePath in documentationItems.items() :
	item = scriptWindowMenu.item( "/Help/" + label )
	if item is not None :
		item.command = functools.partial( __showLocalDocumentation, relativePath )
