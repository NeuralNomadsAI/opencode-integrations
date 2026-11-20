import functools
import os
import pathlib
import weakref

import IECore
import imath

import Gaffer
import GafferUI
import GafferOpenCode

from Qt import QtCore
from Qt import QtGui

from .Client import ClientProcess


class OpenCodeEditor( GafferUI.Editor ) :

	def __init__( self, scriptNode, **kw ) :

		self.__column = GafferUI.ListContainer(
			GafferUI.ListContainer.Orientation.Vertical,
			spacing = 6,
			borderWidth = 6,
		)
		GafferUI.Editor.__init__( self, self.__column, scriptNode, **kw )
		self._qtWidget().setObjectName( "gafferOpenCodeEditor" )

		self.__header = GafferUI.ListContainer( GafferUI.ListContainer.Orientation.Horizontal, spacing = 6 )
		self.__brand = GafferUI.Label( "OpenCode for Gaffer" )
		self.__brand._qtWidget().setObjectName( "gafferOpenCodeBrand" )
		self.__conversations = []
		self.__selected = None
		self.__conversationButton = GafferUI.MenuButton( "New conversation", menu = GafferUI.Menu( Gaffer.WeakMethod( self.__conversationMenu ) ) )
		self.__models = []
		self.__model = ""
		self.__modelButton = GafferUI.MenuButton(
			"MODEL  loading",
			menu = GafferUI.Menu( Gaffer.WeakMethod( self.__modelMenuDefinition ), title = "Model" ),
		)
		self.__modelButton.setEnabled( False )
		self.__header.append( self.__brand )
		self.__header.append( GafferUI.Spacer( imath.V2i( 1 ), maximumSize = imath.V2i( 100000, 1 ) ), expand = True )
		self.__statusLabel = GafferUI.Label( "CONNECTING" )
		self.__statusLabel._qtWidget().setObjectName( "gafferOpenCodeStatus" )
		self.__header.append( self.__statusLabel )
		self.__header.append( self.__modelButton )
		self.__header.append( self.__conversationButton )
		self.__column.append( self.__header )

		self.__transcript = GafferUI.MultiLineTextWidget(
			editable = False,
			wrapMode = GafferUI.MultiLineTextWidget.WrapMode.Word,
			role = GafferUI.MultiLineTextWidget.Role.Code,
		)
		self.__transcript._qtWidget().setObjectName( "gafferOpenCodeTranscript" )
		self.__column.append( self.__transcript, expand = True )

		self.__permissionRow = GafferUI.ListContainer( GafferUI.ListContainer.Orientation.Horizontal, spacing = 6 )
		self.__permissionLabel = GafferUI.Label( "" )
		self.__allowButton = GafferUI.Button( "Allow once" )
		self.__rejectButton = GafferUI.Button( "Reject" )
		self.__permissionRow.append( self.__permissionLabel )
		self.__permissionRow.append( GafferUI.Spacer( imath.V2i( 1 ), maximumSize = imath.V2i( 100000, 1 ) ), expand = True )
		self.__permissionRow.append( self.__rejectButton )
		self.__permissionRow.append( self.__allowButton )
		self.__permissionRow.setVisible( False )
		self.__column.append( self.__permissionRow )

		self.__promptRow = GafferUI.ListContainer( GafferUI.ListContainer.Orientation.Horizontal, spacing = 6 )
		self.__prompt = GafferUI.MultiLineTextWidget(
			placeholderText = "Ask OpenCode about this Gaffer script...  Ctrl+Enter to send",
			fixedLineHeight = 4,
			wrapMode = GafferUI.MultiLineTextWidget.WrapMode.Word,
		)
		self.__sendButton = GafferUI.Button( "Send" )
		self.__sendButton.setEnabled( True )
		self.__stopButton = GafferUI.Button( "Stop" )
		self.__stopButton.setEnabled( False )
		self.__promptRow.append( self.__prompt, expand = True )
		self.__promptRow.append( self.__stopButton )
		self.__promptRow.append( self.__sendButton )
		self.__column.append( self.__promptRow )

		self._qtWidget().setStyleSheet(
			"#gafferOpenCodeEditor { background: #202326; }"
			"#gafferOpenCodeBrand { color: #61d6c4; font-weight: 700; letter-spacing: 1px; }"
			"#gafferOpenCodeSession { color: #969da3; }"
			"#gafferOpenCodeStatus { color: #d5a657; font-weight: 600; }"
			"#gafferOpenCodeTranscript { border-top: 1px solid #34393e; border-bottom: 1px solid #34393e; }"
		)

		self.__sendButton.clickedSignal().connect( Gaffer.WeakMethod( self.__send ) )
		self.__stopButton.clickedSignal().connect( Gaffer.WeakMethod( self.__stop ) )
		self.__allowButton.clickedSignal().connect( Gaffer.WeakMethod( self.__allow ) )
		self.__rejectButton.clickedSignal().connect( Gaffer.WeakMethod( self.__reject ) )
		self.__prompt.activatedSignal().connect( Gaffer.WeakMethod( self.__send ) )

		self.__permissionRequest = None
		self.__streaming = False
		self.__busy = False
		# A plain client: Gaffer offers this script to OpenCode by itself (GafferOpenCode), whether or not a
		# panel is open; the panel only attaches its own conversation to that offer.
		self.__client = None
		self.__channel = None
		self.__connected = False
		if not GafferOpenCode.scriptHandle( scriptNode ) :
			raise RuntimeError( "OpenCodeEditor requires a ScriptNode registered with the Gaffer application" )
		self.__scriptConnections = [
			scriptNode.plugSetSignal().connect( Gaffer.WeakMethod( self.__scriptPlugSet ), scoped = True ),
		]
		GafferOpenCode.acquirePanel( self.__ensureChannel() )
		self.__target = None
		self.__sync()
		self.__timer = QtCore.QTimer( self._qtWidget() )
		self.__timer.timeout.connect( Gaffer.WeakMethod( self.__sync ) )
		self.__timer.start( 500 )

	def __repr__( self ) :

		return "GafferOpenCodeUI.OpenCodeEditor( scriptNode )"

	## Closing the editor releases its conversation only: the script's offer stays for every other conversation.
	@staticmethod
	def __release( client, channel ) :

		try :
			client.close( channel )
		finally :
			GafferOpenCode.releasePanel( channel )

	def __ensureChannel( self ) :

		if self.__channel is None :
			self.__client = ClientProcess.acquire()
			self.__channel = self.__client.register( self.__event )
			weakref.finalize( self, OpenCodeEditor.__release, self.__client, self.__channel )
		return self.__channel

	def __sync( self ) :
		params = self.__connectParams()
		if params != self.__target :
			self.__target = params
			self.__client.send( self.__channel, "connect", params )

	def __event( self, name, data ) :

		if name == "session" :
			self.__connected = True
			self.__selected = data.get( "id" )
			self.__conversationButton.setToolTip( "Workspace: " + str( data.get( "workspace", "" ) ) )
			self.__setModel( str( data.get( "model", "" ) ) )
			self.__append( "system", "Resumed session" if data.get( "resumed" ) else "New session" )
		elif name == "models" :
			self.__models = list( data.get( "models", [] ) )
			self.__setModel( str( data.get( "selected", "" ) ) )
			self.__modelButton.setEnabled( bool( self.__models ) and not self.__busy )
		elif name == "model" :
			self.__setModel( str( data.get( "selected", "" ) ) )
		elif name == "history" :
			self.__streaming = False
			self.__transcript.setText( "" )
			for message in data.get( "messages", [] ) :
				self.__append( message.get( "role", "system" ), message.get( "text", "" ) )
		elif name == "conversations" :
			self.__conversations = data["entries"]
			self.__selected = data.get( "selected" )
			title = next( (item["title"] for item in self.__conversations if item["id"] == self.__selected), "New conversation" )
			self.__conversationButton.setText( title )
		elif name == "notice" :
			self.__append( "system", data["message"] )
		elif name == "user_message" :
			self.__append( "user", data["text"] )
		elif name == "assistant_start" :
			self.__startAssistant()
		elif name == "assistant_delta" :
			self.__appendDelta( data.get( "text", "" ) )
		elif name == "assistant_end" :
			self.__streaming = False
			self.__appendRaw( "\n" )
		elif name == "tool" :
			self.__append( "tool", str( data.get( "name") or data.get( "id", "tool" ) ) + " / " + str( data.get( "state", "" ) ) )
		elif name == "permission" :
			self.__permissionRequest = data.get( "request_id" )
			resources = ", ".join( data.get( "resources", [] ) )
			self.__permissionLabel.setText( "PERMISSION  " + str( data.get( "action", "" ) ) + ( " / " + resources if resources else "" ) )
			self.__permissionRow.setVisible( True )
		elif name == "status" :
			state = str( data.get( "state", "unknown" ) )
			self.__statusLabel.setText( state.upper() )
			if state == "connecting" :
				self.__connected = False
				self.__permissionRequest = None
				self.__permissionRow.setVisible( False )
			self.__busy = state in ( "busy", "retry" )
			self.__stopButton.setEnabled( self.__busy or state == "connecting" )
			self.__sendButton.setEnabled( not self.__busy )
			self.__prompt.setEnabled( not self.__busy )
			self.__modelButton.setEnabled( self.__connected and bool( self.__models ) and not self.__busy )
		elif name == "error" :
			self.__append( "error", data.get( "message", "Unknown OpenCode error" ) )
			self.__statusLabel.setText( "ERROR" )
			self.__busy = False
			self.__streaming = False
			self.__stopButton.setEnabled( False )
			self.__sendButton.setEnabled( True )
			self.__prompt.setEnabled( True )
			self.__modelButton.setEnabled( self.__connected and bool( self.__models ) )

	def __setModel( self, model ) :

		if not model :
			return
		self.__model = model
		self.__modelButton.setText( "MODEL  " + model.rpartition( "/" )[2] )
		self.__modelButton.setToolTip( model )

	def __modelMenuDefinition( self ) :

		result = IECore.MenuDefinition()
		for model in self.__models :
			key = str( model.get( "provider_id", "" ) ) + "/" + str( model.get( "id", "" ) )
			result.append(
				"/" + key,
				{
					"command" : functools.partial( Gaffer.WeakMethod( self.__selectModel ), model = key ),
					"checkBox" : key == self.__model,
					"active" : not self.__busy,
				},
			)
		if not self.__models :
			result.append( "/No models available", { "active" : False } )
		return result

	def __selectModel( self, unusedMenu = None, model = "" ) :

		if self.__busy or not self.__connected or not model or model == self.__model :
			return
		self.__client.send( self.__channel, "switch_model", { "model" : model } )

	def __conversationMenu( self ) :

		self.__client.send( self.__channel, "list", {} )
		menu = IECore.MenuDefinition()
		menu.append( "/New conversation", { "command" : Gaffer.WeakMethod( self.__newSession ), "checkBox" : self.__selected is None, "active" : not self.__busy } )
		for i, item in enumerate( self.__conversations, 1 ) :
			title = (item.get( "title" ) or "Untitled").replace( "/", "／" )
			menu.append( f"/{i}. {title}", {
				"command" : functools.partial( Gaffer.WeakMethod( self.__openConversation ), item["id"] ),
				"checkBox" : item["id"] == self.__selected,
				"active" : not self.__busy,
			} )
		return menu

	def __openConversation( self, session, unusedMenu = None ) :

		self.__client.send( self.__channel, "open", { "session_id" : session } )

	def __connectParams( self ) :

		scriptNode = self.scriptNode()
		offer = GafferOpenCode.offerInfo( scriptNode )
		fileName = str( scriptNode["fileName"].getValue() )
		params = dict( GafferOpenCode.hostInfo() )
		params.update( {
			"script_handle" : GafferOpenCode.scriptHandle( scriptNode ),
			"offer_label" : offer.get( "label" ),
			"directory" : str( pathlib.Path( fileName ).resolve().parent ) if fileName else os.getcwd(),
		} )
		return params

	def __scriptPlugSet( self, plug ) :

		if not plug.isSame( self.scriptNode()["fileName"] ) :
			return
		self.__sync()

	def __newSession( self, unusedButton = None ) :

		self.__client.send( self.__channel, "new_session", self.__target )

	def __send( self, unusedWidget ) :

		if self.__busy :
			return
		text = self.__prompt.getText().strip()
		if not text :
			return
		self.__prompt.setText( "" )
		self.__client.send( self.__channel, "prompt", { "text" : text } )

	def __stop( self, unusedButton ) :

		self.__client.send( self.__channel, "interrupt", {} )

	def __allow( self, unusedButton ) :

		self.__replyPermission( "once" )

	def __reject( self, unusedButton ) :

		self.__replyPermission( "reject" )

	def __replyPermission( self, reply ) :

		if not self.__permissionRequest :
			return
		self.__client.send(
			self.__channel,
			"permission_reply",
			{ "request_id" : self.__permissionRequest, "reply" : reply },
		)
		self.__permissionRequest = None
		self.__permissionRow.setVisible( False )

	def __format( self, role ) :

		result = QtGui.QTextCharFormat()
		if role == "user" :
			result.setForeground( QtGui.QColor( "#e7e9eb" ) )
		elif role == "assistant" :
			result.setForeground( QtGui.QColor( "#b9ddd7" ) )
		elif role == "tool" :
			result.setForeground( QtGui.QColor( "#d5a657" ) )
		elif role == "error" :
			result.setForeground( QtGui.QColor( "#ef7777" ) )
		else :
			result.setForeground( QtGui.QColor( "#8e969c" ) )
		return result

	def __append( self, role, text ) :

		labels = { "user" : "YOU", "assistant" : "OPENCODE", "tool" : "TOOL", "error" : "ERROR", "system" : "SYSTEM" }
		prefix = labels.get( role, role.upper() )
		if self.__transcript.getText() :
			self.__appendRaw( "\n" )
		self.__appendRaw( prefix + "\n", self.__format( "tool" if role == "tool" else role ) )
		self.__appendRaw( str( text ) + "\n", self.__format( role ) )

	def __startAssistant( self ) :

		if self.__streaming :
			return
		self.__streaming = True
		if self.__transcript.getText() :
			self.__appendRaw( "\n" )
		self.__appendRaw( "OPENCODE\n", self.__format( "assistant" ) )

	def __appendDelta( self, text ) :

		if not self.__streaming :
			self.__startAssistant()
		self.__appendRaw( str( text ), self.__format( "assistant" ) )

	def __appendRaw( self, text, characterFormat = None ) :

		cursor = self.__transcript._qtWidget().textCursor()
		cursor.movePosition( QtGui.QTextCursor.End )
		if characterFormat is not None :
			cursor.setCharFormat( characterFormat )
		cursor.insertText( text )
		self.__transcript._qtWidget().setTextCursor( cursor )
		self.__transcript._qtWidget().ensureCursorVisible()


GafferUI.Editor.registerType( "OpenCode for Gaffer", OpenCodeEditor )
