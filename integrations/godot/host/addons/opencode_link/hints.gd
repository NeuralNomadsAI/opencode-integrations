@tool
extends RefCounted
## What THIS Godot knows about a failed run, read from its ClassDB and the open scene: the closest class, property,
## method or signal names, a method's real signature, the closest children of a node path that was not found.

const MAX_NAMES := 6


static func _closest(word: String, names: Array, limit := MAX_NAMES) -> Array:
	var lower := word.to_lower()
	var scored := []
	for name in names:
		var text := str(name)
		var score := lower.similarity(text.to_lower())
		if text.to_lower().contains(lower) or lower.contains(text.to_lower()):
			score += 0.3
		if score > 0.35:
			scored.append([score, text])
	scored.sort_custom(func(a, b): return a[0] > b[0])
	var out := []
	for item in scored:
		if not item[1] in out:
			out.append(item[1])
		if out.size() >= limit:
			break
	return out


static func type_name(info: Dictionary) -> String:
	if info.get("class_name", &"") != &"":
		return str(info.class_name)
	if info.type == TYPE_NIL:
		return "Variant" if info.get("usage", 0) & PROPERTY_USAGE_NIL_IS_VARIANT else "void"
	return type_string(info.type)


## A method as its class declares it: name(arg: Type = default, ...) -> Return.
static func signature(method: Dictionary) -> String:
	var args := []
	var defaults: Array = method.get("default_args", [])
	var first_default: int = method.args.size() - defaults.size()
	for i in method.args.size():
		var arg: Dictionary = method.args[i]
		var text := "%s: %s" % [arg.name, type_name(arg)]
		if i >= first_default:
			text += " = %s" % var_to_str(defaults[i - first_default])
		args.append(text)
	if method.get("flags", 0) & METHOD_FLAG_VARARG:
		args.append("...")
	var prefix := "static " if method.get("flags", 0) & METHOD_FLAG_STATIC else ""
	return "%s%s(%s) -> %s" % [prefix, method.name, ", ".join(args), type_name(method.get("return", {"type": TYPE_NIL}))]


static func _class_of(name: String) -> String:
	if ClassDB.class_exists(name):
		return name
	for entry in ProjectSettings.get_global_class_list():
		if entry["class"] == name:
			return entry.base
	return ""


static func _methods(class_name_: String) -> Array:
	return ClassDB.class_get_method_list(class_name_) if ClassDB.class_exists(class_name_) else []


static func _method(class_name_: String, name: String) -> Variant:
	for method in _methods(class_name_):
		if method.name == name:
			return method
	return null


static func _members(class_name_: String) -> Array:
	var names := []
	for property in ClassDB.class_get_property_list(class_name_):
		var grouping := PROPERTY_USAGE_CATEGORY | PROPERTY_USAGE_GROUP | PROPERTY_USAGE_SUBGROUP
		if property.usage & (PROPERTY_USAGE_EDITOR | PROPERTY_USAGE_STORAGE | PROPERTY_USAGE_SCRIPT_VARIABLE) and not (property.usage & grouping):
			names.append(property.name)
	return names


static func _property(class_name_: String, name: String) -> Variant:
	for property in ClassDB.class_get_property_list(class_name_):
		if property.name == name:
			return property
	return null


static func _signatures(class_name_: String, names: Array) -> String:
	var out := []
	for name in names:
		var method: Variant = _method(class_name_, name)
		if method != null:
			out.append(signature(method))
	return "; ".join(out)


static func _members_hint(base: String, name: String) -> String:
	var klass := _class_of(base)
	if klass == "":
		return ""
	var properties := _closest(name, _members(klass))
	var methods := _closest(name, _methods(klass).map(func(method): return method.name))
	var parts := []
	if properties:
		parts.append("closest properties of %s: %s" % [klass, ", ".join(properties)])
	if methods:
		parts.append("closest methods: %s" % _signatures(klass, methods.slice(0, 4)))
	return ". ".join(parts)


static func _signature_hint(base: String, name: String) -> String:
	var klass := _class_of(base)
	var method: Variant = _method(klass, name) if klass != "" else null
	if method != null:
		return "%s.%s" % [klass, signature(method)]
	# The class is not said (a parse error): every class that declares the method.
	var found := []
	for candidate in ClassDB.get_class_list():
		var own: Variant = null
		for item in ClassDB.class_get_method_list(candidate, true):
			if item.name == name:
				own = item
				break
		if own != null:
			found.append("%s.%s" % [candidate, signature(own)])
		if found.size() >= 4:
			break
	return "; ".join(found)


## The node a "Node not found" error was relative to, and the closest children where the path broke.
static func _node_hint(path: String, relative: String) -> String:
	var tree := Engine.get_main_loop() as SceneTree
	var base: Node = tree.root.get_node_or_null(NodePath(relative)) if tree != null and relative != "" else null
	if base == null:
		return ""
	var node := base
	for part in path.split("/", false):
		if part == "..":
			node = node.get_parent()
		elif part != ".":
			var next := node.get_node_or_null(NodePath(part))
			if next == null:
				var names := node.get_children().map(func(child): return str(child.name))
				var closest := _closest(part, names)
				return "No child %s under %s; its children: %s" % [part, node.name, ", ".join(closest if closest else names.slice(0, 12))]
			node = next
		if node == null:
			return ""
	return ""


static func _identifier_hint(name: String) -> String:
	var names := Array(ClassDB.get_class_list()) + Array(Engine.get_singleton_list())
	for type in TYPE_MAX:
		names.append(type_string(type))
	for entry in ProjectSettings.get_global_class_list():
		names.append(entry["class"])
	names += ["scene", "PARAMS", "result", "undo_redo", "EditorInterface"]
	var closest := _closest(name, names)
	return "closest names: %s" % ", ".join(closest) if closest else ""


static func _match(pattern: String, text: String) -> RegExMatch:
	return RegEx.create_from_string(pattern).search(text)


static func explain(message: String, _root: Node, logged: Array) -> String:
	var hints := []
	var found: RegExMatch
	found = _match("Identifier \"(\\w+)\" not declared", message)
	if found:
		hints.append(_identifier_hint(found.get_string(1)))
	found = _match("(?:assignment of|access to) property or key '([^']+)'(?: with value of type '[^']+')? on a base object of type '([^']+)'", message)
	if found:
		var property: Variant = _property(_class_of(found.get_string(2)), found.get_string(1)) if _class_of(found.get_string(2)) != "" else null
		if property != null:
			hints.append("%s.%s is %s" % [found.get_string(2), found.get_string(1), type_name(property)])
		else:
			hints.append(_members_hint(found.get_string(2), found.get_string(1)))
	found = _match("Nonexistent function '(\\w+)' in base '([^']+)'", message)
	if found:
		hints.append(_members_hint(found.get_string(2), found.get_string(1)))
	found = _match("(?:Cannot find member|Cannot find property) \"(\\w+)\" in base \"([^\"]+)\"", message)
	if found:
		hints.append(_members_hint(found.get_string(2), found.get_string(1)))
	found = _match("Function \"(\\w+)\\(\\)\" not found in base (\\w+)", message)
	if found:
		# A call of an unknown name on self: a mistyped class, type or function.
		hints.append(_identifier_hint(found.get_string(1)) if found.get_string(2) == "self" else _members_hint(found.get_string(2), found.get_string(1)))
	found = _match("(?:Invalid call to function|Invalid type in function) '(\\w+)' in base '([^']+)'", message)
	if found:
		hints.append(_signature_hint(found.get_string(2), found.get_string(1)))
	found = _match("(?:Too (?:few|many) arguments for|Invalid argument for) \"(\\w+)\\(\\)\"", message)
	if found:
		hints.append(_signature_hint("", found.get_string(1)))
	for entry in logged:
		var text: String = entry.code + " " + entry.rationale
		found = _match("Node not found: \"([^\"]+)\" \\(relative to \"([^\"]+)\"\\)", text)
		if found:
			hints.append(_node_hint(found.get_string(1), found.get_string(2)))
			break
	var out := []
	for hint in hints:
		if hint != "" and not hint in out:
			out.append(hint)
	return ". ".join(out).left(1500)
