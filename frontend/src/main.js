import { createApp } from "vue";
import { createRouter, createWebHistory } from "vue-router";

import App from "./App.vue";
import HistoryPage from "./pages/HistoryPage.vue";
import IncidentPage from "./pages/IncidentPage.vue";
import StatusPage from "./pages/StatusPage.vue";
import SubscriptionPage from "./pages/SubscriptionPage.vue";
import "./style.css";

const router = createRouter({
	history: createWebHistory(),
	routes: [
		{ path: "/", component: StatusPage },
		{ path: "/history", component: HistoryPage },
		{ path: "/incidents/:name", component: IncidentPage, props: true },
		{ path: "/i/:token", component: IncidentPage, props: true },
		{ path: "/subscription/:token", component: SubscriptionPage, props: true },
	],
});

createApp(App).use(router).mount("#app");
