<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'

interface WeatherData {
  location: {
    name: string
    country: string
    admin1: string
  }
  current: {
    temperature: number
    feels_like: number
    humidity: number
    wind_speed: number
    wind_direction: number
    pressure: number
    precipitation: number
    weather_code: number
    condition: string
    icon: string
  }
  forecast: Array<{
    date: string
    weather_code: number
    condition: string
    icon: string
    temp_max: number
    temp_min: number
    precipitation: number
    wind_max: number
    sunrise: string
    sunset: string
  }>
}

interface Typhoon {
  id: string
  name: string
  ename: string
  type: string
  status: string
}

const isVisible = ref(false)
const isLoading = ref(false)
const weatherData = ref<WeatherData | null>(null)
const typhoons = ref<Typhoon[]>([])
const selectedCity = ref('北京')
const errorMsg = ref('')
const searchCity = ref('')
const isSearching = ref(false)
const searchResults = ref<Array<{name: string, country: string, admin1: string}>>([])
const cityTab = ref<'cn' | 'global'>('cn')

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
]

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
]

const currentCities = computed(() => cityTab.value === 'cn' ? cnCities : globalCities)

const emit = defineEmits<{
  (e: 'close'): void
}>()

function toggle() {
  isVisible.value = !isVisible.value
  if (isVisible.value && !weatherData.value) {
    fetchWeather()
  }
}

function close() {
  isVisible.value = false
}

async function fetchWeather() {
  isLoading.value = true
  errorMsg.value = ''
  
  try {
    const response = await fetch(`/api/plugins/weather-alert/call/tool_get_weather_alert`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ args: { city: selectedCity.value } }),
    })
    
    if (!response.ok) {
      throw new Error('Failed to fetch weather')
    }
    
    const result = await response.json()
    if (result.error) {
      throw new Error(result.error)
    }
    
    weatherData.value = result
  } catch (e: any) {
    errorMsg.value = e.message || '获取天气失败'
  } finally {
    isLoading.value = false
  }
}

async function searchCityWeather() {
  if (!searchCity.value.trim()) return
  
  isSearching.value = true
  selectedCity.value = searchCity.value.trim()
  await fetchWeather()
  isSearching.value = false
}

async function fetchTyphoons() {
  try {
    const response = await fetch(`/api/plugins/weather-alert/call/tool_get_typhoon_info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ args: {} }),
    })
    
    if (response.ok) {
      const result = await response.json()
      typhoons.value = result.typhoons || []
    }
  } catch (e) {
    console.error('Failed to fetch typhoons:', e)
  }
}

async function changeCity(city: string) {
  selectedCity.value = city
  await fetchWeather()
}

function formatDate(dateStr: string) {
  const date = new Date(dateStr)
  const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
  return `${date.getMonth() + 1}/${date.getDate()} ${weekdays[date.getDay()]}`
}

function formatTime(timeStr: string) {
  if (!timeStr) return '--'
  const date = new Date(timeStr)
  return `${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`
}

function getWindDirection(degrees: number) {
  const directions = ['北', '东北', '东', '东南', '南', '西南', '西', '西北']
  const index = Math.round(degrees / 45) % 8
  return directions[index]
}

onMounted(() => {
  fetchTyphoons()
})
</script>

<template>
  <div class="weather-alert-container">
    <button class="weather-toggle" @click="toggle" :class="{ active: isVisible }">
      <span class="weather-icon">🌤️</span>
      <span class="weather-text">天气</span>
      <span v-if="typhoons.length > 0" class="typhoon-badge">{{ typhoons.length }}</span>
    </button>
    
    <Transition name="popup">
      <div v-if="isVisible" class="weather-popup" @click.self="close">
        <div class="popup-content">
          <div class="popup-header">
            <h3>天气预警</h3>
            <button class="close-btn" @click="close">×</button>
          </div>
          
          <div class="city-selector">
            <div class="city-search">
              <input 
                v-model="searchCity" 
                type="text" 
                placeholder="搜索城市..." 
                class="city-input"
                @keyup.enter="searchCityWeather"
              />
              <button class="search-btn" @click="searchCityWeather" :disabled="isSearching">
                {{ isSearching ? '...' : '搜索' }}
              </button>
            </div>
            <div class="city-tabs">
              <button :class="['tab-btn', { active: cityTab === 'cn' }]" @click="cityTab = 'cn'">
                <span class="tab-icon">🇨🇳</span> 国内
              </button>
              <button :class="['tab-btn', { active: cityTab === 'global' }]" @click="cityTab = 'global'">
                <span class="tab-icon">🌍</span> 国际
              </button>
            </div>
            <div class="city-tags">
              <button 
                v-for="city in currentCities" 
                :key="city.name"
                :class="['city-btn', { active: selectedCity === city.name }]"
                @click="changeCity(city.name)"
              >
                <span class="city-name">{{ city.name }}</span>
                <span class="city-region">{{ city.region }}</span>
              </button>
            </div>
          </div>
          
          <div v-if="isLoading" class="loading-state">
            <div class="spinner"></div>
            <p>获取天气数据中...</p>
          </div>
          
          <div v-else-if="errorMsg" class="error-state">
            <p>{{ errorMsg }}</p>
            <button @click="fetchWeather" class="retry-btn">重试</button>
          </div>
          
          <div v-else-if="weatherData" class="weather-content">
            <div class="current-weather">
              <div class="weather-main">
                <span class="weather-icon-large">{{ weatherData.current.icon }}</span>
                <div class="temp-info">
                  <span class="temperature">{{ weatherData.current.temperature }}°C</span>
                  <span class="condition">{{ weatherData.current.condition }}</span>
                </div>
              </div>
              
              <div class="weather-details">
                <div class="detail-item">
                  <span class="label">体感温度</span>
                  <span class="value">{{ weatherData.current.feels_like }}°C</span>
                </div>
                <div class="detail-item">
                  <span class="label">湿度</span>
                  <span class="value">{{ weatherData.current.humidity }}%</span>
                </div>
                <div class="detail-item">
                  <span class="label">风速</span>
                  <span class="value">{{ weatherData.current.wind_speed }} km/h {{ getWindDirection(weatherData.current.wind_direction) }}</span>
                </div>
                <div class="detail-item">
                  <span class="label">气压</span>
                  <span class="value">{{ weatherData.current.pressure }} hPa</span>
                </div>
                <div class="detail-item">
                  <span class="label">降水</span>
                  <span class="value">{{ weatherData.current.precipitation }} mm</span>
                </div>
                <div class="detail-item">
                  <span class="label">天气代码</span>
                  <span class="value">{{ weatherData.current.weather_code }}</span>
                </div>
              </div>
              
              <div class="sun-times">
                <div class="sun-item">
                  <span class="sun-icon">🌅</span>
                  <span>日出 {{ formatTime(weatherData.forecast[0]?.sunrise) }}</span>
                </div>
                <div class="sun-item">
                  <span class="sun-icon">🌇</span>
                  <span>日落 {{ formatTime(weatherData.forecast[0]?.sunset) }}</span>
                </div>
              </div>
            </div>
            
            <div class="forecast-section" v-if="weatherData.forecast.length > 0">
              <h4>未来天气</h4>
              <div class="forecast-grid">
                <div v-for="(day, index) in weatherData.forecast" :key="index" class="forecast-card">
                  <div class="forecast-header">
                    <span class="forecast-date">{{ formatDate(day.date) }}</span>
                    <span class="forecast-icon">{{ day.icon }}</span>
                  </div>
                  <div class="forecast-body">
                    <span class="forecast-condition">{{ day.condition }}</span>
                    <span class="forecast-temp">{{ day.temp_min }}°C ~ {{ day.temp_max }}°C</span>
                  </div>
                  <div class="forecast-footer">
                    <span class="forecast-detail">
                      <span class="rain-icon">💧</span>{{ day.precipitation }}mm
                    </span>
                    <span class="forecast-detail">
                      <span class="wind-icon">💨</span>{{ day.wind_max }}km/h
                    </span>
                  </div>
                  <div class="forecast-sun">
                    <span>🌅 {{ formatTime(day.sunrise) }}</span>
                    <span>🌇 {{ formatTime(day.sunset) }}</span>
                  </div>
                </div>
              </div>
            </div>
            
            <div class="typhoon-section" v-if="typhoons.length > 0">
              <h4>🌀 台风信息</h4>
              <div class="typhoon-list">
                <div v-for="typhoon in typhoons" :key="typhoon.id" class="typhoon-item">
                  <div class="typhoon-name">
                    <span class="typhoon-icon">🌀</span>
                    <span>{{ typhoon.name }}</span>
                    <span class="typhoon-ename">{{ typhoon.ename }}</span>
                  </div>
                  <div class="typhoon-status">
                    <span class="status-badge">{{ typhoon.type }}</span>
                    <span class="status-text">{{ typhoon.status }}</span>
                  </div>
                </div>
              </div>
              <a href="https://typhoon.nmc.cn/" target="_blank" class="typhoon-link">
                查看台风实时路径 →
              </a>
            </div>
            
            <div class="no-typhoon" v-else>
              <span class="check-icon">✅</span>
              <span>当前无活跃台风</span>
            </div>
          </div>
          
          <div class="source-info">
            <span>数据来源：Open-Meteo | 中央气象台</span>
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.weather-alert-container {
  position: relative;
}

.weather-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border: 1px solid var(--border);
  border-radius: 20px;
  background: var(--surface);
  color: var(--text);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
  position: relative;
}

.weather-toggle:hover {
  border-color: var(--primary);
  background: rgba(var(--primary-rgb, 59, 130, 246), 0.05);
}

.weather-toggle.active {
  border-color: var(--primary);
  background: rgba(var(--primary-rgb, 59, 130, 246), 0.1);
}

.weather-icon {
  font-size: 16px;
}

.typhoon-badge {
  position: absolute;
  top: -4px;
  right: -4px;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 9px;
  background: #ef4444;
  color: white;
  font-size: 11px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.1); }
}

.weather-popup {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  backdrop-filter: blur(4px);
}

.popup-content {
  width: 95%;
  max-width: 700px;
  max-height: 85vh;
  background: var(--surface);
  border-radius: 16px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.popup-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
}

.popup-header h3 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--text);
}

.close-btn {
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 24px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}

.close-btn:hover {
  background: rgba(0, 0, 0, 0.05);
  color: var(--text);
}

.city-selector {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
  background: var(--bg);
}

.city-search {
  display: flex;
  gap: 8px;
}

.city-input {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text);
  font-size: 13px;
  outline: none;
  transition: border-color 0.15s;
}

.city-input:focus {
  border-color: var(--primary);
}

.search-btn {
  padding: 8px 16px;
  border: none;
  border-radius: 8px;
  background: var(--primary);
  color: white;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s;
}

.search-btn:hover:not(:disabled) {
  background: color-mix(in srgb, var(--primary) 80%, black);
}

.search-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.city-tabs {
  display: flex;
  gap: 8px;
}

.tab-btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text-secondary);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
}

.tab-btn:hover {
  border-color: var(--primary);
  color: var(--primary);
}

.tab-btn.active {
  background: var(--primary);
  border-color: var(--primary);
  color: white;
}

.tab-icon {
  font-size: 14px;
}

.city-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.city-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
  min-width: 60px;
}

.city-name {
  font-weight: 500;
  color: var(--text);
}

.city-region {
  font-size: 10px;
  color: var(--text-secondary);
  margin-top: 2px;
}

.city-btn:hover {
  border-color: var(--primary);
  background: rgba(var(--primary-rgb, 59, 130, 246), 0.05);
}

.city-btn.active {
  background: var(--primary);
  border-color: var(--primary);
  color: white;
}

.city-btn.active .city-region {
  color: rgba(255, 255, 255, 0.8);
}

.city-btn.active .city-province {
  color: rgba(255, 255, 255, 0.8);
}

.loading-state, .error-state {
  padding: 40px 20px;
  text-align: center;
  color: var(--text-secondary);
}

.spinner {
  width: 32px;
  height: 32px;
  border: 3px solid var(--border);
  border-top-color: var(--primary);
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin: 0 auto 12px;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.retry-btn {
  margin-top: 12px;
  padding: 8px 16px;
  border: 1px solid var(--primary);
  border-radius: 8px;
  background: transparent;
  color: var(--primary);
  font-size: 13px;
  cursor: pointer;
}

.retry-btn:hover {
  background: rgba(var(--primary-rgb, 59, 130, 246), 0.1);
}

.weather-content {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
}

.current-weather {
  margin-bottom: 20px;
}

.weather-main {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;
}

.weather-icon-large {
  font-size: 56px;
}

.temp-info {
  display: flex;
  flex-direction: column;
}

.temperature {
  font-size: 42px;
  font-weight: 600;
  color: var(--text);
  line-height: 1;
}

.condition {
  font-size: 16px;
  color: var(--text-secondary);
  margin-top: 4px;
}

.weather-details {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
  margin-bottom: 16px;
}

.detail-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px;
  background: var(--bg);
  border-radius: 10px;
}

.detail-item .label {
  font-size: 11px;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.detail-item .value {
  font-size: 14px;
  font-weight: 500;
  color: var(--text);
}

.sun-times {
  display: flex;
  gap: 16px;
}

.sun-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-secondary);
}

.sun-icon {
  font-size: 16px;
}

.forecast-section, .typhoon-section {
  margin-top: 20px;
  padding-top: 20px;
  border-top: 1px solid var(--border);
}

.forecast-section h4, .typhoon-section h4 {
  margin: 0 0 12px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
}

.forecast-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}

.forecast-card {
  background: var(--bg);
  border-radius: 12px;
  padding: 14px;
  border: 1px solid var(--border);
  transition: all 0.15s;
}

.forecast-card:hover {
  border-color: var(--primary);
  box-shadow: 0 2px 8px rgba(var(--primary-rgb, 59, 130, 246), 0.1);
}

.forecast-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.forecast-date {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
}

.forecast-icon {
  font-size: 24px;
}

.forecast-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 8px;
}

.forecast-condition {
  font-size: 13px;
  font-weight: 500;
  color: var(--text);
}

.forecast-temp {
  font-size: 14px;
  font-weight: 600;
  color: var(--primary);
}

.forecast-footer {
  display: flex;
  gap: 12px;
  padding-top: 8px;
  border-top: 1px solid var(--border);
}

.forecast-detail {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--text-secondary);
}

.rain-icon, .wind-icon {
  font-size: 12px;
}

.forecast-sun {
  display: flex;
  justify-content: space-between;
  margin-top: 6px;
  font-size: 10px;
  color: var(--text-secondary);
}

.typhoon-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.typhoon-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px;
  background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%);
  border-radius: 10px;
  border: 1px solid #f59e0b;
}

.typhoon-name {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: #92400e;
}

.typhoon-icon {
  font-size: 20px;
}

.typhoon-ename {
  font-size: 12px;
  font-weight: 400;
  color: #a16207;
}

.typhoon-status {
  display: flex;
  align-items: center;
  gap: 8px;
}

.status-badge {
  padding: 4px 10px;
  border-radius: 12px;
  background: #f59e0b;
  color: white;
  font-size: 11px;
  font-weight: 600;
}

.status-text {
  font-size: 12px;
  color: #92400e;
}

.typhoon-link {
  display: block;
  margin-top: 12px;
  padding: 10px;
  text-align: center;
  color: var(--primary);
  font-size: 13px;
  text-decoration: none;
  border: 1px dashed var(--primary);
  border-radius: 8px;
  transition: all 0.15s;
}

.typhoon-link:hover {
  background: rgba(var(--primary-rgb, 59, 130, 246), 0.05);
}

.no-typhoon {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: 20px;
  padding: 16px;
  background: linear-gradient(135deg, #d1fae5 0%, #a7f3d0 100%);
  border-radius: 10px;
  color: #065f46;
  font-size: 14px;
  font-weight: 500;
}

.check-icon {
  font-size: 18px;
}

.source-info {
  padding: 12px 20px;
  background: var(--bg);
  border-top: 1px solid var(--border);
  text-align: center;
  font-size: 11px;
  color: var(--text-secondary);
}

/* Transitions */
.popup-enter-active, .popup-leave-active {
  transition: opacity 0.2s ease;
}

.popup-enter-active .popup-content, .popup-leave-active .popup-content {
  transition: transform 0.2s ease;
}

.popup-enter-from, .popup-leave-to {
  opacity: 0;
}

.popup-enter-from .popup-content {
  transform: scale(0.95) translateY(10px);
}

.popup-leave-to .popup-content {
  transform: scale(0.95) translateY(10px);
}
</style>
