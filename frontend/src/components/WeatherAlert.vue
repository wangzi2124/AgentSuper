<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue'
import { addAuthHeaders } from '../api/fetch'

// 天气数据接口定义
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

// 台风信息接口定义
interface Typhoon {
  id: string
  name: string
  ename: string
  type: string
  status: string
}

// 弹窗显示状态
const isVisible = ref(false)
// 加载状态
const isLoading = ref(false)
// 天气数据
const weatherData = ref<WeatherData | null>(null)
// 台风列表
const typhoons = ref<Typhoon[]>([])
// 当前选中的城市
const selectedCity = ref('')
// 错误信息
const errorMsg = ref('')
// 搜索城市关键词
const searchCity = ref('')
// 搜索中状态
const isSearching = ref(false)
// 搜索结果
const searchResults = ref<Array<{name: string, country: string, admin1: string}>>([])
// 城市分类标签
const cityTab = ref<'cn' | 'global'>('cn')

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
]

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
]

// 根据当前标签返回对应城市列表
const currentCities = computed(() => cityTab.value === 'cn' ? cnCities : globalCities)

// 定义组件事件：关闭 / 显示状态变化（受控 prop 双向同步）
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'update:show', value: boolean): void
}>()

const props = defineProps<{ show?: boolean }>()

// 受控显示：外部通过 :show 打开，内部关闭时向父级同步
watch(() => props.show, (v) => {
  isVisible.value = !!v
})

// 切换弹窗显示状态
function toggle() {
  isVisible.value = !isVisible.value
  emit('update:show', isVisible.value)
  if (isVisible.value && !weatherData.value) {
    fetchWeather()
  }
}

// 关闭弹窗
function close() {
  isVisible.value = false
  emit('update:show', false)
}

// 获取天气数据
async function fetchWeather() {
  isLoading.value = true
  errorMsg.value = ''
  
  try {
    // 未选择城市时直接读缓存（启动时已自动加载）
    const useCache = !selectedCity.value
    const url = useCache ? '/api/weather' : '/api/weather/refresh'
    const init: RequestInit = useCache
      ? { method: 'GET' }
      : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ city: selectedCity.value }) }

    const response = await fetch(url, { ...init, headers: await addAuthHeaders(init.headers) })
    
    if (!response.ok) {
      throw new Error('Failed to fetch weather')
    }
    
    const result = await response.json()
    if (result.error) {
      throw new Error(result.error)
    }
    
    weatherData.value = result.weather
    if (result.city) {
      selectedCity.value = result.city
    }
  } catch (e: any) {
    errorMsg.value = e.message || '获取天气失败'
  } finally {
    isLoading.value = false
  }
}

// 搜索城市天气
async function searchCityWeather() {
  if (!searchCity.value.trim()) return
  
  isSearching.value = true
  selectedCity.value = searchCity.value.trim()
  await fetchWeather()
  isSearching.value = false
}

// 获取台风信息
async function fetchTyphoons() {
  try {
    const response = await fetch(`/api/plugins/weather-alert/call/tool_get_typhoon_info`, {
      method: 'POST',
      headers: await addAuthHeaders({ 'Content-Type': 'application/json' }),
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

// 切换城市并获取天气
async function changeCity(city: string) {
  selectedCity.value = city
  await fetchWeather()
}

// 格式化日期显示
function formatDate(dateStr: string) {
  const date = new Date(dateStr)
  const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
  return `${date.getMonth() + 1}/${date.getDate()} ${weekdays[date.getDay()]}`
}

// 格式化时间显示
function formatTime(timeStr: string) {
  if (!timeStr) return '--'
  const date = new Date(timeStr)
  return `${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`
}

// 根据角度获取风向
function getWindDirection(degrees: number) {
  const directions = ['北', '东北', '东', '东南', '南', '西南', '西', '西北']
  const index = Math.round(degrees / 45) % 8
  return directions[index]
}

// 组件挂载时获取台风信息
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


<style scoped src="../styles/chat/weatherAlert.css"></style>
