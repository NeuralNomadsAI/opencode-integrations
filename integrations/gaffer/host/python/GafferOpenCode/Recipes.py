import fnmatch
import functools
import hashlib
import inspect
import json

import Gaffer
import GafferScene


REGISTRY_VERSION = 3

_FALLBACK_SPECS = (
	( Gaffer, "Node", "/Utility/Node" ),
	( Gaffer, "Box", None ),
	( Gaffer, "Dot", "/Utility/Dot" ),
	( GafferScene, "Sphere", "/Scene/Source/Primitive/Sphere" ),
	( GafferScene, "Cube", "/Scene/Source/Primitive/Cube" ),
	( GafferScene, "Plane", "/Scene/Source/Primitive/Plane" ),
	( GafferScene, "Camera", "/Scene/Source/Camera" ),
	( GafferScene, "Group", "/Scene/Hierarchy/Group" ),
	( GafferScene, "Parent", "/Scene/Hierarchy/Parent" ),
	( GafferScene, "Transform", "/Scene/Transform/Transform" ),
	( GafferScene, "StandardOptions", "/Scene/Globals/Standard Options" ),
	( GafferScene, "Outputs", "/Scene/Globals/Outputs" ),
	( GafferScene, "Render", "/Scene/Rendering/Render" ),
	( GafferScene, "Set", "/Scene/Globals/Set" ),
	( GafferScene, "PathFilter", "/Scene/Filters/Path Filter" ),
	( GafferScene, "UnionFilter", "/Scene/Filters/Union Filter" ),
	( GafferScene, "SetFilter", "/Scene/Filters/Set Filter" ),
	( GafferScene, "FilterQuery", "/Scene/Utility/Filter Query" ),
	( GafferScene, "SceneReader", "/Scene/File/Reader" ),
	( GafferScene, "SceneWriter", "/Scene/File/Writer" ),
	( GafferScene, "ShaderAssignment", "/Scene/Attributes/Shader Assignment" ),
	( GafferScene, "Attributes", None ),
	( GafferScene, "CustomAttributes", "/Scene/Attributes/Custom Attributes" ),
	( GafferScene, "Prune", "/Scene/Hierarchy/Prune" ),
	( GafferScene, "DeleteObject", "/Scene/Object/Delete Object" ),
	( GafferScene, "Duplicate", "/Scene/Hierarchy/Duplicate" ),
	( GafferScene, "Instancer", "/Scene/Source/Instancer" ),
	( GafferScene, "MergeScenes", "/Scene/Hierarchy/Merge" ),
)


def canonicalID( recipeID ) :

	if "::" not in recipeID and "." in recipeID and not recipeID.startswith( "menu:" ) :
		namespace, separator, typeName = recipeID.rpartition( "." )
		if separator :
			return namespace + "::" + typeName
	return recipeID


def _argumentNames( callable ) :

	try :
		return inspect.signature( callable ).parameters
	except ( TypeError, ValueError ) :
		return {}


def _creatorInfo( creator ) :

	candidates = [ creator ]
	variant = None
	if isinstance( creator, functools.partial ) :
		candidates.extend( creator.args )
		candidates.extend( creator.keywords.values() if creator.keywords else [] )
		variant = next( ( item for item in creator.args if isinstance( item, str ) ), None )

	nodeType = None
	for candidate in candidates :
		if not isinstance( candidate, type ) :
			continue
		try :
			if not issubclass( candidate, Gaffer.Node ) :
				continue
		except TypeError :
			continue
		moduleName = candidate.__module__.split( ".", 1 )[0]
		nodeType = moduleName + "::" + candidate.__name__
		break

	return nodeType, variant


def _nodeMenuRecipe( path, item ) :

	command = getattr( item, "command", None )
	if not callable( command ) :
		return None
	try :
		nonlocals = inspect.getclosurevars( command ).nonlocals
	except ( TypeError, ValueError ) :
		return None
	creator = nonlocals.get( "nodeCreator" )
	if creator is None :
		return None
	nodeType, variant = _creatorInfo( creator )
	return {
		"id" : "menu:" + path,
		"creator" : creator,
		"plug_values" : dict( nonlocals.get( "plugValues", {} ) ),
		"post_creator" : nonlocals.get( "postCreator" ),
		"menu_path" : path,
		"node_type" : nodeType,
		"variant" : variant,
		"source" : "node_menu",
		"requires_menu" : "menu" in _argumentNames( creator ),
		"search_text" : getattr( item, "searchText", None ),
	}


class Registry :

	def __init__( self, applicationRoot = None ) :

		self.__applicationRoot = applicationRoot
		self.__recipes = {}
		self.__menuSignature = None
		self.__capabilityHash = None
		for module, typeName, menuPath in _FALLBACK_SPECS :
			recipeID = module.__name__ + "::" + typeName
			self.__recipes[recipeID] = {
				"id" : recipeID,
				"creator" : getattr( module, typeName ),
				"plug_values" : {},
				"post_creator" : None,
				"menu_path" : menuPath,
				"node_type" : recipeID,
				"variant" : None,
				"source" : "host" if menuPath is None else "fallback",
				"requires_menu" : False,
				"search_text" : None,
			}

	def __refresh( self ) :

		if self.__applicationRoot is None :
			return
		try :
			import GafferUI
			items = list( GafferUI.NodeMenu.acquire( self.__applicationRoot ).definition().items() )
		except Exception :
			return

		signature = tuple( path for path, unusedItem in items )
		if signature == self.__menuSignature :
			return

		for recipeID in [ key for key in self.__recipes if key.startswith( "menu:" ) ] :
			del self.__recipes[recipeID]
		for path, item in items :
			recipe = _nodeMenuRecipe( path, item )
			if recipe is not None :
				self.__recipes[recipe["id"]] = recipe
		self.__menuSignature = signature
		self.__capabilityHash = None

	def ids( self ) :

		self.__refresh()
		return tuple( sorted( self.__recipes ) )

	def nodeTypes( self ) :

		self.__refresh()
		return tuple( sorted( {
			recipe["node_type"] for recipe in self.__recipes.values()
			if recipe["node_type"] is not None
		} ) )

	def catalogue( self, match = None, limit = None ) :

		self.__refresh()
		match = match or {}
		idPattern = str( match.get( "id", "*" ) )
		typePattern = str( match.get( "node_type", "*" ) )
		menuPattern = str( match.get( "menu_path", "*" ) )
		variantPattern = str( match.get( "variant", "*" ) )
		result = []
		for recipeID, recipe in sorted( self.__recipes.items() ) :
			if not fnmatch.fnmatchcase( recipeID, idPattern ) :
				continue
			if not fnmatch.fnmatchcase( recipe["node_type"] or "", typePattern ) :
				continue
			if not fnmatch.fnmatchcase( recipe["menu_path"] or "", menuPattern ) :
				continue
			if not fnmatch.fnmatchcase( recipe["variant"] or "", variantPattern ) :
				continue
			result.append( {
				"id" : recipeID,
				"version" : REGISTRY_VERSION,
				"node_type" : recipe["node_type"],
				"variant" : recipe["variant"],
				"source" : recipe["source"],
				"menu_path" : recipe["menu_path"],
				"search_text" : recipe["search_text"],
				"requires_menu" : recipe["requires_menu"],
				"has_post_creator" : recipe["post_creator"] is not None,
			} )
			if limit is not None and len( result ) >= limit :
				break
		return result

	def create( self, recipeID, variant = None ) :

		self.__refresh()
		recipeID = canonicalID( recipeID )
		if recipeID.startswith( "/" ) :
			recipeID = "menu:" + recipeID
		recipe = self.__recipes.get( recipeID )
		if recipe is None :
			matches = [
				item for item in self.__recipes.values()
				if item["node_type"] == recipeID and ( variant is None or item["variant"] == variant )
			]
			if len( matches ) == 1 :
				recipe = matches[0]
			elif len( matches ) > 1 :
				raise KeyError( "Ambiguous node type; use a menu recipe id or variant: " + recipeID )
		if recipe is None :
			raise KeyError( recipeID )

		creator = recipe["creator"]
		node = creator( menu = None ) if recipe["requires_menu"] else creator()
		return node, recipe

	def capabilityHash( self, operations, methods ) :

		self.__refresh()
		if self.__capabilityHash is None :
			self.__capabilityHash = hashlib.sha256(
				json.dumps(
					{
						"registry" : REGISTRY_VERSION,
						"operations" : list( operations ),
						"methods" : list( methods ),
						"node_recipes" : self.catalogue(),
					},
					sort_keys = True,
				).encode( "utf-8" )
			).hexdigest()
		return self.__capabilityHash


_DEFAULT_REGISTRY = Registry()


def create( recipeID ) :

	return _DEFAULT_REGISTRY.create( recipeID )[0]


def catalogue() :

	return _DEFAULT_REGISTRY.catalogue()


def ids() :

	return _DEFAULT_REGISTRY.ids()
