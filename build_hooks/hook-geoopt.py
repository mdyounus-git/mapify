# geoopt (PLONK's manifold library) compiles functions with TorchScript when
# it is imported, and TorchScript reads their source: with bytecode alone
# the packaged self-test failed ("Can't get source for <function sign>").
# Its .py files go in beside the bytecode, as kornia's do.
module_collection_mode = "pyz+py"
