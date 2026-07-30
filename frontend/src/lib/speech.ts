/** 不指定 voice 時瀏覽器拿 OS 預設語音（常是中文語音），唸英文會怪腔怪調，
 *  所以主動挑英文語音：macOS 本機 Samantha > Chrome 的 Google US English > 任何 en。
 *  Chrome 的 getVoices() 首次呼叫可能還沒載完回空陣列，此時退回 lang 提示，
 *  下一次點擊就會命中。 */
function pickEnglishVoice(): SpeechSynthesisVoice | undefined {
  const voices = window.speechSynthesis.getVoices()
  return (
    voices.find(v => v.name === 'Samantha') ??
    voices.find(v => v.name === 'Google US English') ??
    voices.find(v => v.lang === 'en-US') ??
    voices.find(v => v.lang.startsWith('en'))
  )
}

/** 瀏覽器內建 Web Speech API 唸英文單字/句子。
 *  PronounceButton 的最終 fallback 與測驗聽力題共用。 */
export function speakWord(text: string, onDone?: () => void): void {
  if (!('speechSynthesis' in window)) {
    onDone?.()
    return
  }
  window.speechSynthesis.cancel()
  const utter = new SpeechSynthesisUtterance(text)
  utter.lang = 'en-US'
  const voice = pickEnglishVoice()
  if (voice) utter.voice = voice
  // cancel() 會讓被中斷的 utterance 走 onerror，一樣視為結束
  utter.onend = () => onDone?.()
  utter.onerror = () => onDone?.()
  window.speechSynthesis.speak(utter)
}
