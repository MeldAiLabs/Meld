set(INC
  ../include
  ../../blenkernel
  ../../blenlib
  ../../blentranslation
  ../../gpu
  ../../makesrna
  ../../windowmanager
  ../../../../intern/guardedalloc
  ${CMAKE_BINARY_DIR}/source/blender/makesrna
)

set(SRC
  space_sidebar.cc
)

set(LIB
  bf_blenkernel
  bf_blenlib
  PRIVATE bf::blenloader
  PRIVATE bf::blentranslation
  PRIVATE bf::dna
  PRIVATE bf::gpu
  PRIVATE bf::intern::guardedalloc
  PRIVATE bf::windowmanager
)

blender_add_lib(bf_editor_space_sidebar "${SRC}" "${INC}" "${INC_SYS}" "${LIB}")

add_dependencies(bf_editor_space_sidebar bf_rna)
