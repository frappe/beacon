import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

export default defineConfig({
	plugins: [vue()],
	build: {
		outDir: "../beacon/public/frontend",
		emptyOutDir: true,
	},
	server: {
		port: 8090,
		proxy: { "/api": "http://beacon.localhost:8000" },
	},
});
