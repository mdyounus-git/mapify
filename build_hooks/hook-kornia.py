# kornia (the second matcher's DISK and LightGlue) compiles functions with
# TorchScript when it is imported, and TorchScript reads their source: with
# bytecode alone the packaged app failed its self-test ("Can't get source
# for sampson_epipolar_distance"). Its .py files go in beside the bytecode.
module_collection_mode = "pyz+py"
