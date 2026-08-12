<script setup lang="ts">
import { ref, computed } from 'vue'

// 天气数据接口定义
interface WeatherData {
  location: { name: string; country: string; admin1: string }
  current: {
    temperature: number; feels_like: number; humidity: number
    wind_speed: number; wind_direction: number; pressure: number
    precipitation: number; weather_code: number; condition: string; icon: string
  }
  forecast: Array<{
    date: string; weather_code: number; condition: string; icon: string
    temp_max: number; temp_min: number; precipitation: number; wind_max: number
    sunrise: string; sunset: string
  }>
}

// 面板是否可见
const isVisible = ref(false)
// 是否正在加载天气数据
const isLoading = ref(false)
// 天气数据
const weatherData = ref<WeatherData | null>(null)
// 当前选中的城市
const selectedCity = ref('北京')
// 错误提示信息
const errorMsg = ref('')
// 城市分类标签：cn-国内 global-国际
const cityTab = ref<'cn' | 'global'>('cn')
// 搜索框输入内容
const searchCity = ref('')

// 国内城市列表
const cnCities = [
  { name: '北京', region: '北京' }, { name: '上海', region: '上海' },
  { name: '广州', region: '广东' }, { name: '深圳', region: '广东' },
  { name: '杭州', region: '浙江' }, { name: '成都', region: '四川' },
  { name: '武汉', region: '湖北' }, { name: '西安', region: '陕西' },
  { name: '南京', region: '江苏' }, { name: '重庆', region: '重庆' },
  { name: '大连', region: '辽宁' }, { name: '青岛', region: '山东' },
  { name: '厦门', region: '福建' }, { name: '哈尔滨', region: '黑龙江' },
  { name: '沈阳', region: '辽宁' }, { name: '昆明', region: '云南' },
]

// 国际城市列表
const globalCities = [
  { name: 'Tokyo', region: 'Japan' }, { name: 'New York', region: 'USA' },
  { name: 'London', region: 'UK' }, { name: 'Paris', region: 'France' },
  { name: 'Seoul', region: 'Korea' }, { name: 'Singapore', region: 'Singapore' },
  { name: 'Sydney', region: 'Australia' }, { name: 'Dubai', region: 'UAE' },
  { name: 'Los Angeles', region: 'USA' }, { name: 'Berlin', region: 'Germany' },
  { name: 'Bangkok', region: 'Thailand' }, { name: 'Toronto', region: 'Canada' },
]

// 根据当前标签返回对应城市列表
const currentCities = computed(() => cityTab.value === 'cn' ? cnCities : globalCities)

// 从API获取天气数据
async function fetchWeather() {
  isLoading.value = true
  errorMsg.value = ''
  try {
    const { addAuthHeaders } = await import('../api/fetch')
    const response = await fetch('/api/plugins/weather-alert/call/tool_get_weather_alert', {
      method: 'POST',
      headers: await addAuthHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ args: { city: selectedCity.value } }),
    })
    if (!response.ok) throw new Error('Failed to fetch weather')
    const result = await response.json()
    if (result.error) throw new Error(result.error)
    weatherData.value = result
  } catch (e: any) {
    errorMsg.value = e.message || '获取天气失败'
  } finally {
    isLoading.value = false
  }
}

// 搜索城市天气
async function searchWeather() {
  if (!searchCity.value.trim()) return
  selectedCity.value = searchCity.value.trim()
  await fetchWeather()
}

// 切换选中城市并获取天气
function changeCity(city: string) {
  selectedCity.value = city
  fetchWeather()
}

// 切换面板显示/隐藏状态
function toggle() {
  isVisible.value = !isVisible.value
  if (isVisible.value && !weatherData.value) {
    fetchWeather()
  }
}

// 关闭面板
function close() {
  isVisible.value = false
}

// 根据风向角度返回中文风向
function getWindDirection(degrees: number) {
  const directions = ['北', '东北', '东', '东南', '南', '西南', '西', '西北']
  return directions[Math.round(degrees / 45) % 8]
}

// 格式化日期为 "月/日 周几"
function formatDate(dateStr: string) {
  const date = new Date(dateStr)
  const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
  return `${date.getMonth() + 1}/${date.getDate()} ${weekdays[date.getDay()]}`
}

// 格式化时间为 "HH:MM"
function formatTime(timeStr: string) {
  if (!timeStr) return '--'
  const date = new Date(timeStr)
  return `${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`
}
</script>

<template>
  <div class="m-weather">
    <button class="weather-toggle" @click="toggle">🌤️</button>
    
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="isVisible" class="weather-overlay" @click.self="close">
          <div class="weather-panel">
            <div class="panel-header">
              <h3>天气预警</h3>
              <button class="close-btn" @click="close">×</button>
            </div>
            
            <div class="city-tabs">
              <button :class="['tab', { active: cityTab === 'cn' }]" @click="cityTab = 'cn'">🇨🇳 国内</button>
              <button :class="['tab', { active: cityTab === 'global' }]" @click="cityTab = 'global'">🌍 国际</button>
            </div>
            
            <div class="city-search">
              <input v-model="searchCity" placeholder="搜索城市..." @keyup.enter="searchWeather" />
              <button @click="searchWeather">搜索</button>
            </div>
            
            <div class="city-grid">
              <button v-for="city in currentCities" :key="city.name"
                :class="['city-chip', { active: selectedCity === city.name }]"
                @click="changeCity(city.name)">
                {{ city.name }}
              </button>
            </div>
            
            <div v-if="isLoading" class="loading">加载中...</div>
            <div v-else-if="errorMsg" class="error">{{ errorMsg }}</div>
            <div v-else-if="weatherData" class="weather-content">
              <div class="current">
                <span class="icon">{{ weatherData.current.icon }}</span>
                <div class="temp">
                  <span class="value">{{ weatherData.current.temperature }}°C</span>
                  <span class="label">{{ weatherData.current.condition }}</span>
                </div>
              </div>
              
              <div class="details">
                <div class="item"><span>体感</span><span>{{ weatherData.current.feels_like }}°C</span></div>
                <div class="item"><span>湿度</span><span>{{ weatherData.current.humidity }}%</span></div>
                <div class="item"><span>风速</span><span>{{ weatherData.current.wind_speed }}km/h {{ getWindDirection(weatherData.current.wind_direction) }}</span></div>
                <div class="item"><span>气压</span><span>{{ weatherData.current.pressure }}hPa</span></div>
              </div>
              
              <div v-if="weatherData.forecast.length" class="forecast">
                <h4>未来天气</h4>
                <div v-for="day in weatherData.forecast" :key="day.date" class="forecast-item">
                  <span class="date">{{ formatDate(day.date) }}</span>
                  <span class="icon">{{ day.icon }}</span>
                  <span class="temp">{{ day.temp_min }}°~{{ day.temp_max }}°</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.weather-toggle {
  width: 36px;
  height: 36px;
  border: none;
  border-radius: 8px;
  background: transparent;
  font-size: 20px;
  cursor: pointer;
}

.weather-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: flex-end;
  z-index: 1000;
}

.weather-panel {
  width: 100%;
  max-height: 80vh;
  background: var(--surface, #fff);
  border-radius: 16px 16px 0 0;
  overflow-y: auto;
  padding-bottom: env(safe-area-inset-bottom);
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px;
  border-bottom: 1px solid var(--border, #e2e8f0);
  position: sticky;
  top: 0;
  background: var(--surface, #fff);
}

.panel-header h3 {
  font-size: 16px;
  font-weight: 600;
}

.close-btn {
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 6px;
  background: var(--bg, #f1f5f9);
  font-size: 18px;
  cursor: pointer;
}

.city-tabs {
  display: flex;
  gap: 8px;
  padding: 12px 16px;
}

.tab {
  flex: 1;
  padding: 8px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 8px;
  background: var(--surface, #fff);
  font-size: 13px;
  cursor: pointer;
}

.tab.active {
  background: var(--primary, #3b82f6);
  color: white;
  border-color: var(--primary, #3b82f6);
}

.city-search {
  display: flex;
  gap: 8px;
  padding: 0 16px 12px;
}

.city-search input {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 8px;
  font-size: 14px;
}

.city-search button {
  padding: 8px 16px;
  border: none;
  border-radius: 8px;
  background: var(--primary, #3b82f6);
  color: white;
  font-size: 13px;
}

.city-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 0 16px 16px;
}

.city-chip {
  padding: 6px 12px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 16px;
  background: var(--surface, #fff);
  font-size: 12px;
  cursor: pointer;
}

.city-chip.active {
  background: var(--primary, #3b82f6);
  color: white;
  border-color: var(--primary, #3b82f6);
}

.loading, .error {
  padding: 24px;
  text-align: center;
  color: var(--text-secondary, #64748b);
}

.error {
  color: #ef4444;
}

.weather-content {
  padding: 0 16px 16px;
}

.current {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px;
  background: var(--bg, #f1f5f9);
  border-radius: 12px;
  margin-bottom: 12px;
}

.current .icon {
  font-size: 48px;
}

.current .temp {
  display: flex;
  flex-direction: column;
}

.current .value {
  font-size: 32px;
  font-weight: 600;
}

.current .label {
  font-size: 14px;
  color: var(--text-secondary, #64748b);
}

.details {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
  margin-bottom: 16px;
}

.details .item {
  display: flex;
  justify-content: space-between;
  padding: 10px 12px;
  background: var(--bg, #f1f5f9);
  border-radius: 8px;
  font-size: 12px;
}

.forecast h4 {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 8px;
}

.forecast-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  background: var(--bg, #f1f5f9);
  border-radius: 8px;
  margin-bottom: 6px;
  font-size: 13px;
}

.forecast-item .date {
  width: 70px;
  color: var(--text-secondary, #64748b);
}

.forecast-item .icon {
  font-size: 20px;
}

.forecast-item .temp {
  margin-left: auto;
  font-weight: 500;
}

.fade-enter-active, .fade-leave-active {
  transition: opacity 0.2s;
}

.fade-enter-from, .fade-leave-to {
  opacity: 0;
}
</style>
