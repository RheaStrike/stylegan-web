
import Vue from "vue";
import vSelect from "vue-select";
import App from "./index.vue";

Vue.component("v-select", vSelect);

new Vue({
	render: h => h(App),
}).$mount("#index");
