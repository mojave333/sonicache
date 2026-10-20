"use strict";

// Starts the window. Loaded after every file in js/, so that what init() calls is all there;
// the program's bridge may be in place already or arrive later

if (window.pywebview?.api?.init) boot();
else window.addEventListener("pywebviewready", boot);
