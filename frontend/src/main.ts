/** Application entry point. */

import { mount } from "svelte";
import App from "./App.svelte";
import { installUpgradeGuard } from "./lib/upgrade";
import "./styles/app.css";

// Before mounting: a tab open across an upgrade asks for panel code that no longer
// exists, and the first click on Models or Providers is where it shows.
installUpgradeGuard();

const target = document.getElementById("app");
if (!target) throw new Error("the #app mount point is missing from index.html");

export default mount(App, { target });
