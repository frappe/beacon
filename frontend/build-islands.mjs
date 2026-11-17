// An entry name is the island name that a Vue Component field or a Frappe UI Page names.
import { buildIslands } from "@framework/ui/vite/island";

await buildIslands({
  app: "beacon",
  root: import.meta.dirname,
  entries: {
    "beacon.metrics": "src/islands/metrics.js",
    "beacon.log_viewer": "src/islands/log_viewer.js",
    "beacon.incident_overview": "src/islands/incident_overview.js",
  },
  production: process.argv.includes("--production"),
  watch: process.argv.includes("--watch"),
});
