## A background Godot's main loop (godot --headless --script): loads a scene file as written on disk, runs it for
## PARAMS.frames frames when given (its scripts run, as in the game), then measures the criteria (checks.gd) and
## writes the result to PARAMS.result. Each step is a line OPENCODE_STEP {json} in its log.

var params: Dictionary = {}
var instance: Node
var left := 0
var ran := 0
var began := 0
var capture := Capture.new()


class Capture extends Logger:
	var errors: Array = []

	func _log_error(_function: String, file: String, line: int, code: String, rationale: String, _notify: bool, type: int, _backtraces: Array) -> void:
		if errors.size() < 50 and type != ERROR_TYPE_WARNING:
			errors.append(("%s%s (%s:%d)" % [code, (" " + rationale) if rationale != "" else "", file, line]).left(500))


func step(data: Dictionary) -> void:
	data.at_s = snappedf((Time.get_ticks_msec() - began) / 1000.0, 0.1)
	print("OPENCODE_STEP " + JSON.stringify(data))


func _write(out: Dictionary) -> void:
	var file := FileAccess.open(params.result + ".part", FileAccess.WRITE)
	file.store_string(JSON.stringify(out))
	file.close()
	DirAccess.rename_absolute(params.result + ".part", params.result)


func _initialize() -> void:
	began = Time.get_ticks_msec()
	OS.add_logger(capture)
	var parser := JSON.new()
	var args := OS.get_cmdline_user_args()
	if args.is_empty() or parser.parse(FileAccess.get_file_as_string(args[0])) != OK:
		push_error("OpenCode: no parameters")
		quit(2)
		return
	params = parser.data
	step({"step": "load"})
	var packed: Resource = ResourceLoader.load(params.scene, "PackedScene", ResourceLoader.CACHE_MODE_IGNORE)
	if not (packed is PackedScene):
		_write({"error": "%s did not load as a scene: %s" % [params.scene, "; ".join(capture.errors.slice(0, 5))]})
		quit(1)
		return
	instance = packed.instantiate()
	if instance == null:
		_write({"error": "%s did not instantiate: %s" % [params.scene, "; ".join(capture.errors.slice(0, 5))]})
		quit(1)
		return
	step({"step": "loaded", "seconds": snappedf((Time.get_ticks_msec() - began) / 1000.0, 0.01)})
	params.load_errors = capture.errors.duplicate()
	capture.errors.clear()
	left = int(params.get("frames", 0))
	if left > 0:
		root.add_child(instance)
		current_scene = instance
		step({"step": "running", "frames": left})
	else:
		_finish()


func _process(_delta: float) -> bool:
	if left > 0:
		left -= 1
		ran += 1
		if left == 0:
			_finish()
	return false


func _finish() -> void:
	var out := {"criteria": measure(instance, params.get("criteria", []), step), "frames": ran}
	out.passed = out.criteria.all(func(item): return item.passed)
	if params.get("errors", true):
		out.problems = problems(instance)
	if not params.load_errors.is_empty():
		out.load_errors = params.load_errors
	if not capture.errors.is_empty():
		out.runtime_errors = capture.errors
	out.seconds = snappedf((Time.get_ticks_msec() - began) / 1000.0, 0.01)
	step({"step": "done"})
	_write(out)
	quit(0)
