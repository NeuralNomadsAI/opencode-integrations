## godot_verify on the open scene, in the editor: each criterion measured, and the scene's problems. Read only.

func run():
	if scene == null:
		assert(false, "No scene is open in the editor: open one, or give godot_verify saved with a scene file.")
		return
	result.scene = scene.scene_file_path
	result.criteria = measure(scene, PARAMS.get("criteria", []))
	result.passed = result.criteria.all(func(item): return item.passed)
	if PARAMS.get("errors", true):
		result.problems = problems(scene)
	result.unsaved = scene.scene_file_path == "" or scene.scene_file_path in EditorInterface.get_unsaved_scenes()
