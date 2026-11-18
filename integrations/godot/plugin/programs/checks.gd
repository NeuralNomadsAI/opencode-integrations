## Shared by verify.gd (in the editor, on the open scene) and background.gd (a background Godot, on a file): the
## criteria, measured with Godot's Expression on a scene, and the scene's configuration warnings and script errors.

class Probe extends RefCounted:
	## The functions a criterion's expression can call; `scene` and `root` name the scene's root node.
	var root: Node

	func node(path: String) -> Node:
		return root if path in ["", "."] else root.get_node_or_null(NodePath(path))

	func exists(path: String) -> bool:
		return node(path) != null

	func prop(path: String, name: String) -> Variant:
		var found := node(path)
		return found.get(name) if found != null else null

	func children(path: String = ".") -> Array:
		var found := node(path)
		return found.get_children().map(func(child): return str(child.name)) if found != null else []

	func nodes(kind: String) -> Array:
		var out := []
		for found in _all():
			if found.is_class(kind) or (found.get_script() != null and found.get_script().get_global_name() == kind):
				out.append(path_of(found))
		return out

	func count(kind: String) -> int:
		return nodes(kind).size()

	func in_group(path: String, group: String) -> bool:
		var found := node(path)
		return found != null and found.is_in_group(group)

	func script_of(path: String) -> String:
		var found := node(path)
		return found.get_script().resource_path if found != null and found.get_script() != null else ""

	func path_of(found: Node) -> String:
		return "." if found == root else str(root.get_path_to(found))

	func _all() -> Array:
		var out := [root]
		var stack := [root]
		while not stack.is_empty():
			for child in stack.pop_back().get_children():
				out.append(child)
				stack.append(child)
		return out


static func plain(value: Variant, root: Node, depth := 0) -> Variant:
	if depth > 16:
		return str(value)
	match typeof(value):
		TYPE_NIL, TYPE_BOOL, TYPE_INT, TYPE_STRING:
			return value
		TYPE_FLOAT:
			return value if is_finite(value) else str(value)
		TYPE_STRING_NAME, TYPE_NODE_PATH:
			return str(value)
		TYPE_VECTOR2, TYPE_VECTOR2I:
			return [value.x, value.y]
		TYPE_VECTOR3, TYPE_VECTOR3I:
			return [value.x, value.y, value.z]
		TYPE_VECTOR4, TYPE_VECTOR4I, TYPE_QUATERNION:
			return [value.x, value.y, value.z, value.w]
		TYPE_COLOR:
			return [value.r, value.g, value.b, value.a]
		TYPE_RECT2, TYPE_RECT2I, TYPE_AABB:
			return {"position": plain(value.position, root), "size": plain(value.size, root)}
		TYPE_TRANSFORM3D:
			return {"basis": [plain(value.basis.x, root), plain(value.basis.y, root), plain(value.basis.z, root)], "origin": plain(value.origin, root)}
		TYPE_TRANSFORM2D:
			return {"x": plain(value.x, root), "y": plain(value.y, root), "origin": plain(value.origin, root)}
		TYPE_OBJECT:
			if value == null or not is_instance_valid(value):
				return null
			if value is Node:
				return {"node": "." if value == root else (str(root.get_path_to(value)) if root.is_ancestor_of(value) else str(value.name)), "class": value.get_class()}
			if value is Resource:
				return {"resource": value.resource_path if value.resource_path != "" else null, "class": value.get_class()}
			return {"object": value.get_class()}
		TYPE_DICTIONARY:
			var out := {}
			for key in value:
				out[str(key)] = plain(value[key], root, depth + 1)
			return out
	if typeof(value) >= TYPE_ARRAY:
		var items := []
		for i in mini(value.size(), 1000):
			items.append(plain(value[i], root, depth + 1))
		return items
	return str(value)


static func _numbers(value: Variant) -> Variant:
	if value is int or value is float:
		return [float(value)]
	if value is Array:
		var out := []
		for item in value:
			var inner: Variant = _numbers(item)
			if inner == null:
				return null
			out.append_array(inner)
		return out
	return null


static func compare(value: Variant, op: String, expected: Variant, tol: float) -> bool:
	match op:
		"", "true":
			return bool(value) if value != null else false
		"==":
			return value == expected or (_numbers(value) != null and _numbers(value) == _numbers(expected))
		"!=":
			return not compare(value, "==", expected, tol)
		"approx":
			var a: Variant = _numbers(value)
			var b: Variant = _numbers(expected)
			if a == null or b == null or a.size() != b.size():
				return false
			for i in a.size():
				if absf(a[i] - b[i]) > tol:
					return false
			return true
		"<", "<=", ">", ">=":
			var a: Variant = _numbers(value)
			var b: Variant = _numbers(expected)
			if a == null or b == null or a.size() != b.size():
				return false
			for i in a.size():
				var ok: bool = {"<": a[i] < b[i], "<=": a[i] <= b[i], ">": a[i] > b[i], ">=": a[i] >= b[i]}[op]
				if not ok:
					return false
			return true
		"between":
			return expected is Array and expected.size() == 2 and compare(value, ">=", expected[0], tol) and compare(value, "<=", expected[1], tol)
		"in":
			return expected is Array and expected.any(func(item): return compare(value, "==", item, tol))
		"contains":
			if value is String:
				return value.contains(str(expected))
			return value is Array and value.any(func(item): return compare(item, "==", expected, tol))
	return false


## Each criterion measured on the scene: {label, expr, op, expected, value, passed} or its error.
static func measure(root: Node, criteria: Array, step := Callable()) -> Array:
	var probe := Probe.new()
	probe.root = root
	var results := []
	for index in mini(criteria.size(), 50):
		var criterion: Dictionary = criteria[index] if criteria[index] is Dictionary else {"expr": str(criteria[index])}
		var label := str(criterion.get("label", criterion.get("expr", "")))
		if step.is_valid():
			step.call({"step": "criterion", "index": index + 1, "of": criteria.size(), "label": label})
		var expression := Expression.new()
		var item := {"label": label, "expr": str(criterion.get("expr", "")), "op": str(criterion.get("op", "true"))}
		if criterion.has("value"):
			item.expected = criterion.value
		if expression.parse(item.expr, ["scene", "root"]) != OK:
			item.passed = false
			item.error = expression.get_error_text()
			results.append(item)
			continue
		var value: Variant = expression.execute([root, root], probe, false)
		if expression.has_execute_failed():
			item.passed = false
			item.error = expression.get_error_text()
		else:
			item.value = plain(value, root)
			item.passed = compare(item.value, item.op, criterion.get("value"), float(criterion.get("tol", 0.0001)))
		results.append(item)
	return results


## The scene's nodes whose script does not compile (Godot then runs them without it).
static func problems(root: Node, limit := 50) -> Array:
	var out := []
	var probe := Probe.new()
	probe.root = root
	for found in probe._all():
		var script: Variant = found.get_script()
		if script is Script and not script.can_instantiate():
			out.append({"node": probe.path_of(found), "class": found.get_class(), "script": "%s does not compile" % script.resource_path})
			if out.size() >= limit:
				break
	return out
