"""Hints for a failed execute_code run, computed inside Gaffer in the except block, before anything is undone.

explain( error, code, filename, script ) returns a few lines saying what Gaffer really offers where the code went
wrong, or None. Each hint only reads: children of existing graph components, plug types and values, dir() of
classes and modules, a ProcessException's plug. Measured with Gaffer 1.7.1.0:
  - a missing child:       KeyError "'radiuss' is not a child of 'sphere'"
  - a wrong value type:    Boost.Python.ArgumentError "FloatPlug.setValue(FloatPlug, str) did not match ..."
  - an unknown attribute:  AttributeError (name, obj)
  - a computation error:   Gaffer.ProcessException, naming the plug ("node.plug : message")
  - a refused connection:  IECore.Exception 'Plug "ScriptNode.a.b" rejects input "ScriptNode.c.d".'
"""

import difflib
import re

MAX_CHARS = 1500
MAX_ITEMS = 40

_CHILD = re.compile( r"^'([^']*)' is not a child of '([^']*)'$" )
_ARGUMENTS = re.compile( r"Python argument types in\s+(\w+)\.(\w+)\(([^)]*)\)" )
_REJECTS = re.compile( r'Plug "([^"]+)" rejects input "([^"]+)"' )
_NAME = re.compile( r"name '(\w+)' is not defined" )
GAFFER_MODULES = (
	"Gaffer", "GafferScene", "GafferImage", "GafferDispatch", "GafferOSL", "GafferCycles", "GafferUSD", "GafferArnold",
	"GafferRenderMan", "GafferML", "GafferVDB", "IECore", "IECoreScene", "IECoreImage", "imath",
)


def closest( name, candidates, n = 5 ) :

	"""The candidates closest to name: difflib matches (case-insensitive), then those containing it."""

	pool = sorted( { str( c ) for c in candidates if c } )
	if not name or not pool :
		return []
	lower = { c.lower() : c for c in pool }
	found = [ lower[c] for c in difflib.get_close_matches( name.lower(), list( lower ), n = n, cutoff = 0.5 ) ]
	needle = name.lower()
	found += [ c for c in pool if c not in found and ( needle in c.lower() or ( len( c ) > 2 and c.lower() in needle ) ) ]
	return found[:n]


def _bounded( names, limit = MAX_ITEMS ) :

	items = list( names )
	shown = ", ".join( items[:limit] )
	return shown + ( " ... ({} more)".format( len( items ) - limit ) if len( items ) > limit else "" )


def _components( script ) :

	"""Every node and plug of the script, depth first (bounded)."""

	import Gaffer

	found = []
	pending = [ script ]
	while pending and len( found ) < 20000 :
		component = pending.pop()
		found.append( component )
		pending.extend( reversed( component.children( Gaffer.GraphComponent ) ) )
	return found


def _childHint( error, script ) :

	match = _CHILD.match( str( error.args[0] ) if error.args else "" )
	if not match or script is None :
		return None
	wanted, parentName = match.groups()
	parents = [ c for c in _components( script ) if c.getName() == parentName and wanted not in c ]
	if script.getName() == parentName or parentName == "ScriptNode" :
		parents.insert( 0, script )
	for parent in parents[:3] :
		names = parent.keys()
		near = closest( wanted, names )
		lines = [ "{} ({}) has no child {!r}.".format( parent.relativeName( script ) or parent.getName(), parent.typeName(), wanted ) ]
		if near :
			lines.append( "Closest: " + ", ".join( near ) )
		lines.append( "Its children: " + _bounded( names ) )
		return "\n".join( lines )
	return None


def _valueType( className ) :

	import Gaffer

	cls = getattr( Gaffer, className, None )
	value = getattr( cls, "ValueType", None )
	if value is None :
		return None
	module = getattr( value, "__module__", "builtins" )
	return value.__name__ if module == "builtins" else "{}.{}".format( module, value.__name__ )


def _argumentHint( error ) :

	text = str( error )
	match = _ARGUMENTS.search( text )
	if not match :
		return None
	cls, method, given = match.groups()
	lines = [ "{}.{} was given ({}).".format( cls, method, given.strip() ) ]
	if method == "setValue" :
		value = _valueType( cls )
		if value :
			lines.append( "{}.setValue takes a {} (the plug's ValueType).".format( cls, value ) )
	signature = text.split( "did not match C++ signature:", 1 )
	if len( signature ) == 2 :
		lines.append( "C++ signature: " + " ".join( signature[1].split() )[:400] )
	return "\n".join( lines )


def _attributeHint( error ) :

	name = getattr( error, "name", None )
	obj = getattr( error, "obj", None )
	if not name or obj is None :
		return None
	try :
		names = [ n for n in dir( obj ) if not n.startswith( "_" ) ]
	except Exception :
		return None
	near = closest( name, names )
	label = obj.__name__ if hasattr( obj, "__name__" ) and type( obj ).__name__ == "module" else type( obj ).__name__
	if not near :
		return "{} has no {!r}.".format( label, name )
	return "{} has no {!r}. Closest: {}".format( label, name, ", ".join( near ) )


def _processHint( error, script ) :

	plug = None
	try :
		plug = error.plug()
	except Exception :
		return None
	if plug is None :
		return None
	node = plug.node()
	lines = []
	try :
		process = error.processType()
	except Exception :
		process = "compute"
	if node is not None :
		where = node.relativeName( script ) if script is not None and script.isAncestorOf( node ) else node.fullName()
		lines.append( "Computing {} of {} ({}) failed ({}).".format( plug.getName(), where, node.typeName(), process ) )
		import Gaffer

		if isinstance( node, Gaffer.Expression ) :
			try :
				expression, language = node.getExpression()
				lines.append( "Its {} expression: {}".format( language, expression[:400] ) )
			except Exception :
				pass
		for name in ( "fileName", "filePath" ) :
			if name in node and hasattr( node[name], "getValue" ) :
				try :
					lines.append( "{}: {!r}".format( name, node[name].getValue() ) )
				except Exception :
					pass
		inputs = []
		for child in Gaffer.Plug.RecursiveInputRange( node ) :
			source = child.getInput()
			if source is not None and source.node() is not None and source.node() is not node :
				inputs.append( "{} <- {}".format( child.relativeName( node ), source.node().getName() ) )
		if inputs :
			lines.append( "Its inputs: " + _bounded( inputs, 12 ) )
	return "\n".join( lines ) or None


def _rejectsHint( error, script ) :

	match = _REJECTS.search( str( error ) )
	if not match or script is None :
		return None
	found = []
	for full in match.groups() :
		path = full.split( ".", 1 )[1] if "." in full else full
		plug = script.descendant( path )
		found.append( "{} is a {}".format( path, plug.typeName() if plug is not None else "missing plug" ) )
	return "; ".join( found ) + ": connect plugs of compatible types (an output of a node into an input of another)."


def _nameHint( error, namespace ) :

	match = _NAME.search( str( error ) )
	if not match :
		return None
	name = match.group( 1 )
	lines = []
	if name in GAFFER_MODULES :
		lines.append( "{} is not imported here: import {}".format( name, name ) )
	near = closest( name, list( namespace or {} ) + list( GAFFER_MODULES ) )
	if near and not lines :
		lines.append( "Closest names: " + ", ".join( near ) )
	return "\n".join( lines ) or None


def explain( error, code = None, filename = None, script = None, namespace = None ) :

	import Gaffer

	hint = None
	try :
		if isinstance( error, Gaffer.ProcessException ) :
			hint = _processHint( error, script )
		elif isinstance( error, KeyError ) :
			hint = _childHint( error, script )
		elif type( error ).__name__ == "ArgumentError" :
			hint = _argumentHint( error )
		elif isinstance( error, AttributeError ) :
			hint = _attributeHint( error )
		elif isinstance( error, NameError ) :
			hint = _nameHint( error, namespace )
		elif "rejects input" in str( error ) :
			hint = _rejectsHint( error, script )
	except Exception :
		return None
	return hint[:MAX_CHARS] if hint else None
