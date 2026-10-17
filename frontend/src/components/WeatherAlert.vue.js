/// <reference types="../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { ref, onMounted, computed, watch } from 'vue';
import { addAuthHeaders } from '../api/fetch';
// 弹窗显示状态
const isVisible = ref(false);
// 加载状态
const isLoading = ref(false);
// 天气数据
const weatherData = ref(null);
// 台风列表
const typhoons = ref([]);
// 当前选中的城市
const selectedCity = ref('');
// 错误信息
const errorMsg = ref('');
// 搜索城市关键词
const searchCity = ref('');
// 搜索中状态
const isSearching = ref(false);
// 搜索结果
const searchResults = ref([]);
// 城市分类标签
const cityTab = ref('cn');
// 国内主要城市列表
const cnCities = [
    { name: '北京', region: '北京' },
    { name: '上海', region: '上海' },
    { name: '广州', region: '广东' },
    { name: '深圳', region: '广东' },
    { name: '杭州', region: '浙江' },
    { name: '成都', region: '四川' },
    { name: '武汉', region: '湖北' },
    { name: '西安', region: '陕西' },
    { name: '南京', region: '江苏' },
    { name: '重庆', region: '重庆' },
    { name: '天津', region: '天津' },
    { name: '苏州', region: '江苏' },
    { name: '郑州', region: '河南' },
    { name: '长沙', region: '湖南' },
    { name: '大连', region: '辽宁' },
    { name: '青岛', region: '山东' },
    { name: '厦门', region: '福建' },
    { name: '哈尔滨', region: '黑龙江' },
    { name: '沈阳', region: '辽宁' },
    { name: '昆明', region: '云南' },
    { name: '香港', region: '香港' },
    { name: '澳门', region: '澳门' },
    { name: '台北', region: '台湾' },
];
// 国际城市列表
const globalCities = [
    // 亚洲
    { name: 'Tokyo', region: 'Japan' },
    { name: 'Seoul', region: 'Korea' },
    { name: 'Singapore', region: 'Singapore' },
    { name: 'Bangkok', region: 'Thailand' },
    { name: 'Kuala Lumpur', region: 'Malaysia' },
    { name: 'Jakarta', region: 'Indonesia' },
    { name: 'Manila', region: 'Philippines' },
    { name: 'Ho Chi Minh', region: 'Vietnam' },
    { name: 'Mumbai', region: 'India' },
    { name: 'Delhi', region: 'India' },
    { name: 'Dubai', region: 'UAE' },
    { name: 'Doha', region: 'Qatar' },
    { name: 'Istanbul', region: 'Turkey' },
    { name: 'Tel Aviv', region: 'Israel' },
    // 北美
    { name: 'New York', region: 'USA' },
    { name: 'Los Angeles', region: 'USA' },
    { name: 'San Francisco', region: 'USA' },
    { name: 'Chicago', region: 'USA' },
    { name: 'Las Vegas', region: 'USA' },
    { name: 'Miami', region: 'USA' },
    { name: 'Seattle', region: 'USA' },
    { name: 'Toronto', region: 'Canada' },
    { name: 'Vancouver', region: 'Canada' },
    { name: 'Mexico City', region: 'Mexico' },
    // 欧洲
    { name: 'London', region: 'UK' },
    { name: 'Paris', region: 'France' },
    { name: 'Berlin', region: 'Germany' },
    { name: 'Rome', region: 'Italy' },
    { name: 'Madrid', region: 'Spain' },
    { name: 'Barcelona', region: 'Spain' },
    { name: 'Amsterdam', region: 'Netherlands' },
    { name: 'Brussels', region: 'Belgium' },
    { name: 'Vienna', region: 'Austria' },
    { name: 'Zurich', region: 'Switzerland' },
    { name: 'Munich', region: 'Germany' },
    { name: 'Prague', region: 'Czech' },
    { name: 'Warsaw', region: 'Poland' },
    { name: 'Budapest', region: 'Hungary' },
    { name: 'Stockholm', region: 'Sweden' },
    { name: 'Oslo', region: 'Norway' },
    { name: 'Copenhagen', region: 'Denmark' },
    { name: 'Helsinki', region: 'Finland' },
    { name: 'Dublin', region: 'Ireland' },
    { name: 'Lisbon', region: 'Portugal' },
    { name: 'Athens', region: 'Greece' },
    // 大洋洲
    { name: 'Sydney', region: 'Australia' },
    { name: 'Melbourne', region: 'Australia' },
    { name: 'Auckland', region: 'New Zealand' },
    // 南美
    { name: 'Sao Paulo', region: 'Brazil' },
    { name: 'Buenos Aires', region: 'Argentina' },
    { name: 'Santiago', region: 'Chile' },
    { name: 'Lima', region: 'Peru' },
    { name: 'Bogota', region: 'Colombia' },
    // 非洲
    { name: 'Cairo', region: 'Egypt' },
    { name: 'Cape Town', region: 'South Africa' },
    { name: 'Nairobi', region: 'Kenya' },
    // { name: 'Marrakech', region: 'Morocco' },
];
// 根据当前标签返回对应城市列表
const currentCities = computed(() => cityTab.value === 'cn' ? cnCities : globalCities);
const emit = defineEmits();
const props = defineProps();
// 受控显示：外部通过 :show 打开，内部关闭时向父级同步
watch(() => props.show, (v) => {
    isVisible.value = !!v;
});
// 切换弹窗显示状态
function toggle() {
    isVisible.value = !isVisible.value;
    emit('update:show', isVisible.value);
    if (isVisible.value && !weatherData.value) {
        fetchWeather();
    }
}
// 关闭弹窗
function close() {
    isVisible.value = false;
    emit('update:show', false);
}
// 获取天气数据
async function fetchWeather() {
    isLoading.value = true;
    errorMsg.value = '';
    try {
        // 未选择城市时直接读缓存（启动时已自动加载）
        const useCache = !selectedCity.value;
        const url = useCache ? '/api/weather' : '/api/weather/refresh';
        const init = useCache
            ? { method: 'GET' }
            : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ city: selectedCity.value }) };
        const response = await fetch(url, { ...init, headers: await addAuthHeaders(init.headers) });
        if (!response.ok) {
            throw new Error('Failed to fetch weather');
        }
        const result = await response.json();
        if (result.error) {
            throw new Error(result.error);
        }
        weatherData.value = result.weather;
        if (result.city) {
            selectedCity.value = result.city;
        }
    }
    catch (e) {
        errorMsg.value = e.message || '获取天气失败';
    }
    finally {
        isLoading.value = false;
    }
}
// 搜索城市天气
async function searchCityWeather() {
    if (!searchCity.value.trim())
        return;
    isSearching.value = true;
    selectedCity.value = searchCity.value.trim();
    await fetchWeather();
    isSearching.value = false;
}
// 获取台风信息
async function fetchTyphoons() {
    try {
        const response = await fetch(`/api/plugins/weather-alert/call/tool_get_typhoon_info`, {
            method: 'POST',
            headers: await addAuthHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ args: {} }),
        });
        if (response.ok) {
            const result = await response.json();
            typhoons.value = result.typhoons || [];
        }
    }
    catch (e) {
        console.error('Failed to fetch typhoons:', e);
    }
}
// 切换城市并获取天气
async function changeCity(city) {
    selectedCity.value = city;
    await fetchWeather();
}
// 格式化日期显示
function formatDate(dateStr) {
    const date = new Date(dateStr);
    const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
    return `${date.getMonth() + 1}/${date.getDate()} ${weekdays[date.getDay()]}`;
}
// 格式化时间显示
function formatTime(timeStr) {
    if (!timeStr)
        return '--';
    const date = new Date(timeStr);
    return `${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`;
}
// 根据角度获取风向
function getWindDirection(degrees) {
    const directions = ['北', '东北', '东', '东南', '南', '西南', '西', '西北'];
    const index = Math.round(degrees / 45) % 8;
    return directions[index];
}
// 组件挂载时获取台风信息
onMounted(() => {
    fetchTyphoons();
});
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "weather-alert-container" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.toggle) },
    ...{ class: "weather-toggle" },
    ...{ class: ({ active: __VLS_ctx.isVisible }) },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "weather-icon" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "weather-text" },
});
if (__VLS_ctx.typhoons.length > 0) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "typhoon-badge" },
    });
    (__VLS_ctx.typhoons.length);
}
const __VLS_0 = {}.Transition;
/** @type {[typeof __VLS_components.Transition, typeof __VLS_components.Transition, ]} */ ;
// @ts-ignore
const __VLS_1 = __VLS_asFunctionalComponent(__VLS_0, new __VLS_0({
    name: "popup",
}));
const __VLS_2 = __VLS_1({
    name: "popup",
}, ...__VLS_functionalComponentArgsRest(__VLS_1));
__VLS_3.slots.default;
if (__VLS_ctx.isVisible) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ onClick: (__VLS_ctx.close) },
        ...{ class: "weather-popup" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "popup-content" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "popup-header" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.h3, __VLS_intrinsicElements.h3)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.close) },
        ...{ class: "close-btn" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "city-selector" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "city-search" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        ...{ onKeyup: (__VLS_ctx.searchCityWeather) },
        value: (__VLS_ctx.searchCity),
        type: "text",
        placeholder: "搜索城市...",
        ...{ class: "city-input" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.searchCityWeather) },
        ...{ class: "search-btn" },
        disabled: (__VLS_ctx.isSearching),
    });
    (__VLS_ctx.isSearching ? '...' : '搜索');
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "city-tabs" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.isVisible))
                    return;
                __VLS_ctx.cityTab = 'cn';
            } },
        ...{ class: (['tab-btn', { active: __VLS_ctx.cityTab === 'cn' }]) },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "tab-icon" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.isVisible))
                    return;
                __VLS_ctx.cityTab = 'global';
            } },
        ...{ class: (['tab-btn', { active: __VLS_ctx.cityTab === 'global' }]) },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "tab-icon" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "city-tags" },
    });
    for (const [city] of __VLS_getVForSourceType((__VLS_ctx.currentCities))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.isVisible))
                        return;
                    __VLS_ctx.changeCity(city.name);
                } },
            key: (city.name),
            ...{ class: (['city-btn', { active: __VLS_ctx.selectedCity === city.name }]) },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "city-name" },
        });
        (city.name);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "city-region" },
        });
        (city.region);
    }
    if (__VLS_ctx.isLoading) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "loading-state" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "spinner" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
    }
    else if (__VLS_ctx.errorMsg) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "error-state" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
        (__VLS_ctx.errorMsg);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (__VLS_ctx.fetchWeather) },
            ...{ class: "retry-btn" },
        });
    }
    else if (__VLS_ctx.weatherData) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "weather-content" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "current-weather" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "weather-main" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "weather-icon-large" },
        });
        (__VLS_ctx.weatherData.current.icon);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "temp-info" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "temperature" },
        });
        (__VLS_ctx.weatherData.current.temperature);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "condition" },
        });
        (__VLS_ctx.weatherData.current.condition);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "weather-details" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "detail-item" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "label" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "value" },
        });
        (__VLS_ctx.weatherData.current.feels_like);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "detail-item" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "label" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "value" },
        });
        (__VLS_ctx.weatherData.current.humidity);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "detail-item" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "label" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "value" },
        });
        (__VLS_ctx.weatherData.current.wind_speed);
        (__VLS_ctx.getWindDirection(__VLS_ctx.weatherData.current.wind_direction));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "detail-item" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "label" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "value" },
        });
        (__VLS_ctx.weatherData.current.pressure);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "detail-item" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "label" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "value" },
        });
        (__VLS_ctx.weatherData.current.precipitation);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "detail-item" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "label" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "value" },
        });
        (__VLS_ctx.weatherData.current.weather_code);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "sun-times" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "sun-item" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "sun-icon" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        (__VLS_ctx.formatTime(__VLS_ctx.weatherData.forecast[0]?.sunrise));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "sun-item" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "sun-icon" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        (__VLS_ctx.formatTime(__VLS_ctx.weatherData.forecast[0]?.sunset));
        if (__VLS_ctx.weatherData.forecast.length > 0) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "forecast-section" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.h4, __VLS_intrinsicElements.h4)({});
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "forecast-grid" },
            });
            for (const [day, index] of __VLS_getVForSourceType((__VLS_ctx.weatherData.forecast))) {
                __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                    key: (index),
                    ...{ class: "forecast-card" },
                });
                __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                    ...{ class: "forecast-header" },
                });
                __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                    ...{ class: "forecast-date" },
                });
                (__VLS_ctx.formatDate(day.date));
                __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                    ...{ class: "forecast-icon" },
                });
                (day.icon);
                __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                    ...{ class: "forecast-body" },
                });
                __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                    ...{ class: "forecast-condition" },
                });
                (day.condition);
                __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                    ...{ class: "forecast-temp" },
                });
                (day.temp_min);
                (day.temp_max);
                __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                    ...{ class: "forecast-footer" },
                });
                __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                    ...{ class: "forecast-detail" },
                });
                __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                    ...{ class: "rain-icon" },
                });
                (day.precipitation);
                __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                    ...{ class: "forecast-detail" },
                });
                __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                    ...{ class: "wind-icon" },
                });
                (day.wind_max);
                __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                    ...{ class: "forecast-sun" },
                });
                __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
                (__VLS_ctx.formatTime(day.sunrise));
                __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
                (__VLS_ctx.formatTime(day.sunset));
            }
        }
        if (__VLS_ctx.typhoons.length > 0) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "typhoon-section" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.h4, __VLS_intrinsicElements.h4)({});
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "typhoon-list" },
            });
            for (const [typhoon] of __VLS_getVForSourceType((__VLS_ctx.typhoons))) {
                __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                    key: (typhoon.id),
                    ...{ class: "typhoon-item" },
                });
                __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                    ...{ class: "typhoon-name" },
                });
                __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                    ...{ class: "typhoon-icon" },
                });
                __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
                (typhoon.name);
                __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                    ...{ class: "typhoon-ename" },
                });
                (typhoon.ename);
                __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                    ...{ class: "typhoon-status" },
                });
                __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                    ...{ class: "status-badge" },
                });
                (typhoon.type);
                __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                    ...{ class: "status-text" },
                });
                (typhoon.status);
            }
            __VLS_asFunctionalElement(__VLS_intrinsicElements.a, __VLS_intrinsicElements.a)({
                href: "https://typhoon.nmc.cn/",
                target: "_blank",
                ...{ class: "typhoon-link" },
            });
        }
        else {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "no-typhoon" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "check-icon" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        }
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "source-info" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
}
var __VLS_3;
/** @type {__VLS_StyleScopedClasses['weather-alert-container']} */ ;
/** @type {__VLS_StyleScopedClasses['weather-toggle']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['weather-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['weather-text']} */ ;
/** @type {__VLS_StyleScopedClasses['typhoon-badge']} */ ;
/** @type {__VLS_StyleScopedClasses['weather-popup']} */ ;
/** @type {__VLS_StyleScopedClasses['popup-content']} */ ;
/** @type {__VLS_StyleScopedClasses['popup-header']} */ ;
/** @type {__VLS_StyleScopedClasses['close-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['city-selector']} */ ;
/** @type {__VLS_StyleScopedClasses['city-search']} */ ;
/** @type {__VLS_StyleScopedClasses['city-input']} */ ;
/** @type {__VLS_StyleScopedClasses['search-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['city-tabs']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['tab-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['tab-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['tab-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['tab-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['city-tags']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['city-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['city-name']} */ ;
/** @type {__VLS_StyleScopedClasses['city-region']} */ ;
/** @type {__VLS_StyleScopedClasses['loading-state']} */ ;
/** @type {__VLS_StyleScopedClasses['spinner']} */ ;
/** @type {__VLS_StyleScopedClasses['error-state']} */ ;
/** @type {__VLS_StyleScopedClasses['retry-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['weather-content']} */ ;
/** @type {__VLS_StyleScopedClasses['current-weather']} */ ;
/** @type {__VLS_StyleScopedClasses['weather-main']} */ ;
/** @type {__VLS_StyleScopedClasses['weather-icon-large']} */ ;
/** @type {__VLS_StyleScopedClasses['temp-info']} */ ;
/** @type {__VLS_StyleScopedClasses['temperature']} */ ;
/** @type {__VLS_StyleScopedClasses['condition']} */ ;
/** @type {__VLS_StyleScopedClasses['weather-details']} */ ;
/** @type {__VLS_StyleScopedClasses['detail-item']} */ ;
/** @type {__VLS_StyleScopedClasses['label']} */ ;
/** @type {__VLS_StyleScopedClasses['value']} */ ;
/** @type {__VLS_StyleScopedClasses['detail-item']} */ ;
/** @type {__VLS_StyleScopedClasses['label']} */ ;
/** @type {__VLS_StyleScopedClasses['value']} */ ;
/** @type {__VLS_StyleScopedClasses['detail-item']} */ ;
/** @type {__VLS_StyleScopedClasses['label']} */ ;
/** @type {__VLS_StyleScopedClasses['value']} */ ;
/** @type {__VLS_StyleScopedClasses['detail-item']} */ ;
/** @type {__VLS_StyleScopedClasses['label']} */ ;
/** @type {__VLS_StyleScopedClasses['value']} */ ;
/** @type {__VLS_StyleScopedClasses['detail-item']} */ ;
/** @type {__VLS_StyleScopedClasses['label']} */ ;
/** @type {__VLS_StyleScopedClasses['value']} */ ;
/** @type {__VLS_StyleScopedClasses['detail-item']} */ ;
/** @type {__VLS_StyleScopedClasses['label']} */ ;
/** @type {__VLS_StyleScopedClasses['value']} */ ;
/** @type {__VLS_StyleScopedClasses['sun-times']} */ ;
/** @type {__VLS_StyleScopedClasses['sun-item']} */ ;
/** @type {__VLS_StyleScopedClasses['sun-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['sun-item']} */ ;
/** @type {__VLS_StyleScopedClasses['sun-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['forecast-section']} */ ;
/** @type {__VLS_StyleScopedClasses['forecast-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['forecast-card']} */ ;
/** @type {__VLS_StyleScopedClasses['forecast-header']} */ ;
/** @type {__VLS_StyleScopedClasses['forecast-date']} */ ;
/** @type {__VLS_StyleScopedClasses['forecast-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['forecast-body']} */ ;
/** @type {__VLS_StyleScopedClasses['forecast-condition']} */ ;
/** @type {__VLS_StyleScopedClasses['forecast-temp']} */ ;
/** @type {__VLS_StyleScopedClasses['forecast-footer']} */ ;
/** @type {__VLS_StyleScopedClasses['forecast-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['rain-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['forecast-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['wind-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['forecast-sun']} */ ;
/** @type {__VLS_StyleScopedClasses['typhoon-section']} */ ;
/** @type {__VLS_StyleScopedClasses['typhoon-list']} */ ;
/** @type {__VLS_StyleScopedClasses['typhoon-item']} */ ;
/** @type {__VLS_StyleScopedClasses['typhoon-name']} */ ;
/** @type {__VLS_StyleScopedClasses['typhoon-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['typhoon-ename']} */ ;
/** @type {__VLS_StyleScopedClasses['typhoon-status']} */ ;
/** @type {__VLS_StyleScopedClasses['status-badge']} */ ;
/** @type {__VLS_StyleScopedClasses['status-text']} */ ;
/** @type {__VLS_StyleScopedClasses['typhoon-link']} */ ;
/** @type {__VLS_StyleScopedClasses['no-typhoon']} */ ;
/** @type {__VLS_StyleScopedClasses['check-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['source-info']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            isVisible: isVisible,
            isLoading: isLoading,
            weatherData: weatherData,
            typhoons: typhoons,
            selectedCity: selectedCity,
            errorMsg: errorMsg,
            searchCity: searchCity,
            isSearching: isSearching,
            cityTab: cityTab,
            currentCities: currentCities,
            toggle: toggle,
            close: close,
            fetchWeather: fetchWeather,
            searchCityWeather: searchCityWeather,
            changeCity: changeCity,
            formatDate: formatDate,
            formatTime: formatTime,
            getWindDirection: getWindDirection,
        };
    },
    __typeEmits: {},
    __typeProps: {},
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
    __typeEmits: {},
    __typeProps: {},
});
; /* PartiallyEnd: #4569/main.vue */
