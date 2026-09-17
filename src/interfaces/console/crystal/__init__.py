"""Crystal: the console's 90s-demo software renderer. `Framebuffer` is an RGB pixel surface
with a depth buffer that rasterizes convex polygons with numpy and blits into the TUI two
pixels per cell with half-block glyphs; `Renderer` rotates, projects, culls and shades meshes
into it per pixel; `Shapes` builds the objects out of axis-aligned boxes with chamfered edges."""
