## godot_inspect: what THIS Godot exposes, read from its ClassDB, the project's script classes and the open scene.
## Read only.

func _limit() -> int:
	return clampi(int(PARAMS.get("limit", 40)), 1, 200)


static func _type(info: Dictionary) -> String:
	if str(info.get("class_name", "")) != "":
		return str(info.class_name)
	if info.get("type", TYPE_NIL) == TYPE_NIL:
		return "Variant" if info.get("usage", 0) & PROPERTY_USAGE_NIL_IS_VARIANT else "void"
	return type_string(info.type)


static func _signature(method: Dictionary) -> String:
	var args := []
	var defaults: Array = method.get("default_args", [])
	var first: int = method.args.size() - defaults.size()
	for i in method.args.size():
		var arg: Dictionary = method.args[i]
		args.append("%s: %s%s" % [arg.name, _type(arg), (" = " + var_to_str(defaults[i - first])) if i >= first else ""])
	if method.get("flags", 0) & METHOD_FLAG_VARARG:
		args.append("...")
	return "%s%s(%s) -> %s" % ["static " if method.get("flags", 0) & METHOD_FLAG_STATIC else "", method.name, ", ".join(args), _type(method.get("return", {}))]


static func _signal(info: Dictionary) -> String:
	return "%s(%s)" % [info.name, ", ".join(info.args.map(func(arg): return "%s: %s" % [arg.name, _type(arg)]))]


static func _words(text: String) -> Array:
	return text.to_lower().replace("_", " ").replace(".", " ").split(" ", false)


static func _score(name: String, words: Array) -> int:
	var lower := name.to_lower()
	var flat := lower.replace("_", "")
	var score := 0
	for word in words:
		if lower == word or flat == word:
			score += 4
		elif lower.contains(word) or flat.contains(word):
			score += 2
		else:
			return 0
	return score


func _docs(klass: String) -> String:
	var info := Engine.get_version_info()
	return "https://docs.godotengine.org/en/%d.%d/classes/class_%s.html" % [info.major, info.minor, klass.to_lower()]


func _script_classes() -> Dictionary:
	var out := {}
	for entry in ProjectSettings.get_global_class_list():
		out[str(entry["class"])] = entry
	return out


func _search(text: String) -> Dictionary:
	var words := _words(text)
	var found := []
	for klass in ClassDB.get_class_list():
		var own := _score(klass, words)
		if own:
			found.append([own + 2, {"kind": "class", "class": klass, "inherits": ClassDB.get_parent_class(klass)}])
		for property in ClassDB.class_get_property_list(klass, true):
			if property.usage & (PROPERTY_USAGE_CATEGORY | PROPERTY_USAGE_GROUP | PROPERTY_USAGE_SUBGROUP):
				continue
			var score := _score(property.name, words)
			if score:
				found.append([score, {"kind": "property", "class": klass, "name": property.name, "type": _type(property)}])
		for method in ClassDB.class_get_method_list(klass, true):
			var score := _score(method.name, words)
			if score and not method.name.begins_with("_"):
				found.append([score, {"kind": "method", "class": klass, "signature": _signature(method)}])
		for info in ClassDB.class_get_signal_list(klass, true):
			var score := _score(info.name, words)
			if score:
				found.append([score, {"kind": "signal", "class": klass, "signature": _signal(info)}])
	for name in _script_classes():
		var score := _score(name, words)
		if score:
			var entry: Dictionary = _script_classes()[name]
			found.append([score + 2, {"kind": "script class", "class": name, "inherits": entry.base, "path": entry.path}])
	found.sort_custom(func(a, b): return a[0] > b[0] or (a[0] == b[0] and str(a[1]).length() < str(b[1]).length()))
	var limit := _limit()
	var more := found.slice(limit, limit + 60).map(func(item): return "%s.%s" % [item[1]["class"], item[1].get("name", item[1].get("signature", "")).get_slice("(", 0)])
	return {"words": words, "found": found.size(), "results": found.slice(0, limit).map(func(item): return item[1]), "also": more}


func _wanted(name: String) -> bool:
	var pattern := str(PARAMS.get("match", ""))
	if pattern == "":
		return true
	if pattern.contains("*") or pattern.contains("?"):
		return name.matchn(pattern)
	return _score(name, _words(pattern)) > 0


func _class(klass: String) -> Dictionary:
	var scripts := _script_classes()
	var script: Script = null
	if not ClassDB.class_exists(klass) and scripts.has(klass):
		script = load(scripts[klass].path)
	elif not ClassDB.class_exists(klass):
		var names := Array(ClassDB.get_class_list()) + scripts.keys()
		var close := names.filter(func(name): return klass.to_lower().similarity(name.to_lower()) > 0.5)
		close.sort_custom(func(a, b): return klass.to_lower().similarity(a.to_lower()) > klass.to_lower().similarity(b.to_lower()))
		return {"class": klass, "error": "Godot has no class %s." % klass, "closest": close.slice(0, 8)}
	var inherited: bool = PARAMS.has("match")
	var properties := []
	var methods := []
	var signals := []
	var list_properties: Array = script.get_script_property_list() if script else ClassDB.class_get_property_list(klass, not inherited)
	var list_methods: Array = script.get_script_method_list() if script else ClassDB.class_get_method_list(klass, not inherited)
	var list_signals: Array = script.get_script_signal_list() if script else ClassDB.class_get_signal_list(klass, not inherited)
	for property in list_properties:
		if property.usage & (PROPERTY_USAGE_CATEGORY | PROPERTY_USAGE_GROUP | PROPERTY_USAGE_SUBGROUP) or not _wanted(property.name):
			continue
		var item := {"name": property.name, "type": _type(property)}
		if property.hint_string != "" and property.hint in [PROPERTY_HINT_ENUM, PROPERTY_HINT_RANGE, PROPERTY_HINT_FLAGS]:
			item.hint = property.hint_string.left(200)
		if not script:
			var value: Variant = ClassDB.class_get_property_default_value(klass, property.name)
			if value != null:
				item.default = var_to_str(value).left(120)
		properties.append(item)
	for method in list_methods:
		if not method.name.begins_with("_") or script:
			if _wanted(method.name):
				methods.append(_signature(method))
	for info in list_signals:
		if _wanted(info.name):
			signals.append(_signal(info))
	var limit := _limit()
	var chain := []
	var parent: String = ClassDB.get_parent_class(klass) if not script else str(script.get_instance_base_type())
	while parent != "":
		chain.append(parent)
		parent = ClassDB.get_parent_class(parent)
	var out := {
		"class": klass, "inherits": chain, "properties": properties.slice(0, limit), "methods": methods.slice(0, limit),
		"signals": signals.slice(0, limit),
	}
	if script:
		out.path = script.resource_path
	else:
		out.docs = _docs(klass)
		out.instantiable = ClassDB.can_instantiate(klass)
		var enums := {}
		for name in ClassDB.class_get_enum_list(klass, true):
			enums[name] = Array(ClassDB.class_get_enum_constants(klass, name, true))
		if not enums.is_empty():
			out.enums = enums
	var beyond := {"properties": properties.size() - limit, "methods": methods.size() - limit, "signals": signals.size() - limit}
	for key in beyond:
		if beyond[key] > 0:
			out["%s_beyond_limit" % key] = beyond[key]
	if not inherited:
		out.note = "Own members only: give match (a glob or words) to search the inherited ones too."
	return out


func _node(path: String) -> Dictionary:
	if scene == null:
		return {"error": "No scene is open in the editor."}
	var found: Node = scene if path in ["", "."] else scene.get_node_or_null(NodePath(path))
	if found == null:
		var parent := scene
		var parts := path.split("/", false)
		for part in parts:
			var next := parent.get_node_or_null(NodePath(part))
			if next == null:
				var names := parent.get_children().map(func(child): return str(child.name))
				names.sort_custom(func(a, b): return part.similarity(a) > part.similarity(b))
				return {"node": path, "error": "No child %s under %s." % [part, "." if parent == scene else str(scene.get_path_to(parent))], "children": names.slice(0, 20)}
			parent = next
		return {"node": path, "error": "No node %s." % path}
	var out := {
		"node": "." if found == scene else str(scene.get_path_to(found)), "class": found.get_class(),
		"children": found.get_children().map(func(child): return "%s (%s)" % [child.name, child.get_class()]).slice(0, 50),
		"groups": Array(found.get_groups()).map(func(group): return str(group)).filter(func(group): return not group.begins_with("_")),
	}
	if found.get_script() != null:
		out.script = found.get_script().resource_path
	if found != scene and found.scene_file_path != "":
		out.instance = found.scene_file_path
	var values := {}
	var count := 0
	for property in found.get_property_list():
		if not property.usage & PROPERTY_USAGE_STORAGE or not _wanted(property.name):
			continue
		var value: Variant = found.get(property.name)
		var default: Variant = ClassDB.class_get_property_default_value(found.get_class(), property.name)
		if PARAMS.has("match") or default == null or str(value) != str(default):
			values[property.name] = runner_value(value)
			count += 1
			if count >= _limit():
				break
	out.values = values
	var connections := []
	for info in found.get_signal_list():
		for connection in found.get_signal_connection_list(info.name):
			if connection.flags & Object.CONNECT_PERSIST:
				connections.append("%s -> %s.%s" % [info.name, connection.callable.get_object(), connection.callable.get_method()])
	if connections:
		out.connections = connections.slice(0, 40)
	return out


func runner_value(value: Variant) -> Variant:
	if value is Object:
		if value is Resource:
			return {"resource": value.resource_path if value.resource_path != "" else null, "class": value.get_class()}
		if value is Node:
			return {"node": str(scene.get_path_to(value)) if scene.is_ancestor_of(value) else str(value.name)}
		return {"object": value.get_class()}
	return var_to_str(value).left(300) if typeof(value) in [TYPE_TRANSFORM3D, TYPE_BASIS, TYPE_PROJECTION] else value


func _tree(depth: int) -> Array:
	var lines := []
	if scene == null:
		return lines
	var stack := [[scene, 0]]
	while not stack.is_empty() and lines.size() < 400:
		var item: Array = stack.pop_back()
		var node: Node = item[0]
		lines.append("%s%s (%s)%s" % ["  ".repeat(item[1]), node.name, node.get_class(), (" [%s]" % node.scene_file_path) if node != scene and node.scene_file_path != "" else ""])
		if item[1] < depth:
			var children := node.get_children()
			children.reverse()
			for child in children:
				if child.owner == scene or child.owner == null:
					stack.append([child, item[1] + 1])
	return lines


func run():
	var info := Engine.get_version_info()
	result.godot = info.string
	result.docs = "https://docs.godotengine.org/en/%d.%d/" % [info.major, info.minor]
	if PARAMS.has("search"):
		result.search = _search(str(PARAMS.search))
	if PARAMS.has("class"):
		result["class"] = _class(str(PARAMS["class"]))
	if PARAMS.has("node"):
		result.node = _node(str(PARAMS.node))
	if PARAMS.get("tree"):
		result.tree = _tree(clampi(int(PARAMS.get("depth", 6)), 1, 32))
