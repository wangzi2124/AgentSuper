// 移动端用到的 Vant 组件，须与 src/mobile/** 模板中的 <van-*> 一一对应（勿漏）：
//   action-sheet button cell cell-group empty field icon loading nav-bar
//   picker popup search swipe-cell switch tabbar tabbar-item uploader
// 注意：这里用「字面量」动态 import（不拼路径变量），否则 Vite 会把 vant/es/*/index.mjs
// 全部展开成 glob，反而把整包打进产物。
const COMPONENT_IMPORTS = [
    () => import('vant/es/action-sheet'),
    () => import('vant/es/button'),
    () => import('vant/es/cell'),
    () => import('vant/es/cell-group'),
    () => import('vant/es/empty'),
    () => import('vant/es/field'),
    () => import('vant/es/icon'),
    () => import('vant/es/loading'),
    () => import('vant/es/nav-bar'),
    () => import('vant/es/picker'),
    () => import('vant/es/popup'),
    () => import('vant/es/search'),
    () => import('vant/es/swipe-cell'),
    () => import('vant/es/switch'),
    () => import('vant/es/tabbar'),
    () => import('vant/es/tabbar-item'),
    () => import('vant/es/uploader'),
];
// 组件样式（Vant 样式与 JS 分离，且在 package.json 中被标记为 sideEffects，
// 必须显式引入；每个组件的 style 会自带 base/依赖组件的 CSS）。
const STYLE_IMPORTS = [
    () => import('vant/es/action-sheet/style/index'),
    () => import('vant/es/button/style/index'),
    () => import('vant/es/cell/style/index'),
    () => import('vant/es/cell-group/style/index'),
    () => import('vant/es/empty/style/index'),
    () => import('vant/es/field/style/index'),
    () => import('vant/es/icon/style/index'),
    () => import('vant/es/loading/style/index'),
    () => import('vant/es/nav-bar/style/index'),
    () => import('vant/es/picker/style/index'),
    () => import('vant/es/popup/style/index'),
    () => import('vant/es/search/style/index'),
    () => import('vant/es/swipe-cell/style/index'),
    () => import('vant/es/switch/style/index'),
    () => import('vant/es/tabbar/style/index'),
    () => import('vant/es/tabbar-item/style/index'),
    () => import('vant/es/uploader/style/index'),
    // 函数式组件（showToast / showConfirmDialog / showDialog）无法随模板组件自动带样式，
    // 需显式引入其样式。
    () => import('vant/es/toast/style/index'),
    () => import('vant/es/dialog/style/index'),
];
let installing = null;
/**
 * 确保按需 Vant 组件已注册到 app（幂等，多次调用共享同一个 Promise）。
 * 桌面端永不调用 → 永不加载 Vant。
 */
export function ensureVant(app) {
    if (!installing) {
        installing = Promise.all([
            ...COMPONENT_IMPORTS.map((load) => load()),
            ...STYLE_IMPORTS.map((load) => load()),
        ]).then((mods) => {
            for (let i = 0; i < COMPONENT_IMPORTS.length; i++) {
                const mod = mods[i];
                if (mod && mod.default)
                    app.use(mod.default);
            }
        });
    }
    return installing;
}
/** 是否为移动端视口（与 App.vue / mobile.css 的 768px 断点保持一致）。 */
export function isMobileViewport() {
    if (typeof window === 'undefined' || !window.matchMedia)
        return false;
    return window.matchMedia('(max-width: 768px)').matches;
}
