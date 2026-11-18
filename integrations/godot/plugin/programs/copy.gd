## A working copy of the open scene, as it is now in the editor, for a background Godot (godot_verify frames,
## godot_run): written to PARAMS.file; the open scene keeps its file and its unsaved changes. Read only.

func run():
	if scene == null:
		assert(false, "No scene is open in the editor: open one, or give a scene file.")
		return
	var packed := PackedScene.new()
	var packing := packed.pack(scene)
	if packing != OK:
		assert(false, "The open scene could not be packed: %s" % error_string(packing))
		return
	var saved := ResourceSaver.save(packed, PARAMS.file)
	if saved != OK:
		assert(false, "The working copy could not be written to %s: %s" % [PARAMS.file, error_string(saved)])
		return
	result.file = PARAMS.file
	result.scene = scene.scene_file_path
	result.unsaved = scene.scene_file_path == "" or scene.scene_file_path in EditorInterface.get_unsaved_scenes()
