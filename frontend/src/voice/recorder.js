// 微信式「按住说话」录音控制器。
// 用 MediaRecorder 采集原始音频（webm/ogg），AudioContext + AnalyserNode 取波形峰值，
// 每块回调给 UI 更新时长/波形/取消态。桌面与移动端共用，与端无关。
export const MAX_VOICE_SECONDS = 60;
export const MIN_VOICE_SECONDS = 1;
const PEAK_INTERVAL_MS = 120; // 每 ~120ms 收集一个波形柱
export class PressToTalkRecorder {
    stream = null;
    mediaRecorder = null;
    audioCtx = null;
    analyser = null;
    chunks = [];
    startTime = 0;
    peaks = [];
    peakTimer = null;
    frameTimer = null;
    armedFlag = false;
    canceled = false;
    done = false;
    onFrame = null;
    get isRecording() {
        return !!this.mediaRecorder && this.mediaRecorder.state === 'recording';
    }
    async start(onFrame) {
        this.onFrame = onFrame;
        // 必须在用户手势内调用（Chrome 自动播放策略要求）
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        this.stream = stream;
        this.chunks = [];
        this.peaks = [];
        this.armedFlag = false;
        this.canceled = false;
        this.done = false;
        this.startTime = performance.now();
        const Ctx = window.AudioContext || window.webkitAudioContext;
        this.audioCtx = new Ctx();
        this.analyser = this.audioCtx.createAnalyser();
        this.analyser.fftSize = 1024;
        const src = this.audioCtx.createMediaStreamSource(stream);
        src.connect(this.analyser);
        this.mediaRecorder = new MediaRecorder(stream);
        this.mediaRecorder.ondataavailable = (e) => {
            if (e.data && e.data.size > 0)
                this.chunks.push(e.data);
        };
        this.mediaRecorder.start();
        this.peakTimer = window.setInterval(() => {
            const peak = this.readPeak();
            if (this.peaks.length >= 200)
                this.peaks.shift();
            this.peaks.push(peak);
        }, PEAK_INTERVAL_MS);
        this.frameTimer = window.setInterval(() => {
            const seconds = (performance.now() - this.startTime) / 1000;
            this.onFrame?.({ seconds, peaks: [...this.peaks], armed: this.armedFlag });
        }, 200);
    }
    // 「上滑取消」状态开关（组件根据手势调用；取消态在 stop 时丢弃并清空本地缓冲）
    setArmed(armed) {
        this.armedFlag = armed;
    }
    setCanceled(canceled) {
        this.canceled = canceled;
    }
    readPeak() {
        try {
            const buf = new Uint8Array(this.analyser.fftSize);
            this.analyser.getByteTimeDomainData(buf);
            let sum = 0;
            for (let i = 0; i < buf.length; i++) {
                const v = (buf[i] - 128) / 128;
                sum += v * v;
            }
            const rms = Math.sqrt(sum / buf.length);
            return Math.max(0, Math.min(1, rms * 3));
        }
        catch {
            return 0;
        }
    }
    teardown() {
        if (this.peakTimer) {
            clearInterval(this.peakTimer);
            this.peakTimer = null;
        }
        if (this.frameTimer) {
            clearInterval(this.frameTimer);
            this.frameTimer = null;
        }
        try {
            this.audioCtx?.close();
        }
        catch { /* noop */ }
        this.audioCtx = null;
        this.analyser = null;
        // 释放麦克风
        if (this.stream) {
            this.stream.getTracks().forEach((t) => t.stop());
            this.stream = null;
        }
    }
    async stop() {
        if (this.done)
            return null;
        this.done = true;
        return new Promise((resolve) => {
            if (!this.mediaRecorder || this.mediaRecorder.state !== 'recording') {
                this.teardown();
                resolve(null);
                return;
            }
            this.mediaRecorder.onstop = () => {
                const duration = (performance.now() - this.startTime) / 1000;
                this.teardown();
                if (this.canceled) {
                    this.chunks = [];
                    resolve(null);
                    return;
                }
                const blob = new Blob(this.chunks, { type: this.mediaRecorder?.mimeType || 'audio/webm' });
                this.chunks = [];
                resolve({ blob, duration, peaks: [...this.peaks] });
            };
            this.mediaRecorder.stop();
        });
    }
    // 直接取消（不 await stop 完成）
    cancelNow() {
        this.canceled = true;
        if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
            try {
                this.mediaRecorder.stop();
            }
            catch { /* noop */ }
        }
    }
}
