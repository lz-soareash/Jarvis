/* =============================================================
   JARVIS — SpeechManager (Fase 6b: voz neural + UX de fala)
   - Prepara o texto via backend /api/tts/speech (sanitiza/formata/
     divide em blocos) mantendo o display_text intacto.
   - Fila de fala: reproduz um bloco por vez (nunca 2 ao mesmo tempo).
   - Interruptível (parar/cancelar) e com estado SPEAKING.
   - Neural (Edge/backend) com fallback para voz local do navegador.
   - Falha de TTS NUNCA derruba o JARVIS (silenciosa + flag).
   ============================================================= */
"use strict";

class SpeechManager {
  constructor() {
    this.ttsSupported = typeof window.speechSynthesis !== "undefined";
    this.enabled = localStorage.getItem("jarvis.tts") === "1";

    this.currentAudio = null;
    this.currentUtter = null;
    this.queue = [];
    this.playing = false;
    this.generation = 0;

    this.neuralTts = false;
    this.neuralProbeDone = false;

    this.onStateChange = null;
    this._state = "idle";
  }

  get state() {
    return this._state;
  }

  setState(state) {
    if (this._state === state) return;
    this._state = state;
    if (this.onStateChange) this.onStateChange(state);
  }

  setEnabled(on) {
    this.enabled = !!on;
    localStorage.setItem("jarvis.tts", this.enabled ? "1" : "0");
    if (!this.enabled) this.cancel();
  }

  async probeNeural() {
    if (this.neuralProbeDone) return this.neuralTts;
    this.neuralProbeDone = true;
    try {
      const res = await fetch("/api/tts/ping");
      this.neuralTts = res.ok;
    } catch (_) {
      this.neuralTts = false;
    }
    return this.neuralTts;
  }

  /* Expõe o texto de DISPLAY intacto; usa só o speech_text p/ falar. */
  async prepare(text) {
    if (!text) return null;
    try {
      const res = await fetch(
        `/api/tts/speech?text=${encodeURIComponent(text)}`
      );
      if (!res.ok) return null;
      const data = await res.json();
      const utterances = (data.utterances || []).filter(Boolean);
      return { speechText: data.speech_text, context: data.context, utterances };
    } catch (_) {
      return null;
    }
  }

  /* Fala uma resposta inteira (backlog): enfileira os blocos. */
  async speak(text) {
    if (!this.enabled || !text) return;
    const prepared = await this.prepare(text);
    if (!prepared || !prepared.utterances.length) return;

    const gen = ++this.generation;
    this.queue = this.queue.concat(prepared.utterances);
    this.pump(gen);
  }

  /* Interrompe TUDO (fila + reprodução atual). */
  cancel() {
    this.generation += 1;
    this.queue = [];
    this.nextAudio = null;
    this.stopCurrentAudio();
    this.stopLocalUtter();
    this.playing = false;
    this.setState("idle");
  }

  stopCurrentAudio() {
    if (this.currentAudio) {
      this.currentAudio.pause();
      this.currentAudio.src = "";
      this.currentAudio = null;
    }
  }

  stopLocalUtter() {
    if (this.ttsSupported) {
      try {
        window.speechSynthesis.cancel();
      } catch (_) {}
    }
    this.currentUtter = null;
  }

  async pump(gen) {
    if (gen !== this.generation) return;
    if (!this.enabled) return;
    if (this.playing) return;

    const text = this.queue.shift();
    if (!text) {
      this.setState("idle");
      return;
    }

    this.playing = true;
    this.setState("speaking");

    // Pré-carrega o próximo bloco enquanto o atual toca (transições sem gap).
    this.preloadNext(gen);

    if (await this.probeNeural()) {
      await this.playNeural(text, gen);
    } else {
      this.pauseNeural = null;
      await this.playLocal(text, gen);
    }
  }

  /* Cria (em segundo plano) o áudio do próximo bloco p/ a troca ser imediata. */
  preloadNext(gen) {
    this.nextAudio = null;
    const [text2] = this.queue;
    if (!text2) return;
    const a = new Audio();
    a.preload = "auto";
    a.src = `/api/tts?text=${encodeURIComponent(text2)}`;
    a.oncanplaythrough = () => {
      if (gen === this.generation) this.nextAudio = a;
    };
    a.onerror = () => {
      if (gen === this.generation) this.nextAudio = null;
    };
  }

  playNeural(text, gen) {
    return new Promise((resolve) => {
      // Se já temos o bloco pré-carregado, usa na troca sem espera de rede.
      const audio = this.nextAudio || new Audio(`/api/tts?text=${encodeURIComponent(text)}`);
      this.nextAudio = null;
      this.currentAudio = audio;

      const finish = (failed) => {
        if (gen !== this.generation) return;
        if (this.currentAudio === audio) this.currentAudio = null;
        this.playing = false;
        if (failed) {
          // Fallback silencioso p/ voz local; nunca derruba.
          this.playLocal(text, gen).finally(resolve);
          return;
        }
        this.pump(gen);
        resolve();
      };

      audio.onended = () => finish(false);
      audio.onerror = () => finish(true);
      if (audio.src) {
        audio.play().catch(() => finish(true));
      } else {
        // Não deveria ocorrer: sem src, cai p/ local.
        finish(true);
      }
    });
  }

  playLocal(text, gen) {
    return new Promise((resolve) => {
      if (!this.ttsSupported) {
        this.playing = false;
        this.pump(gen);
        resolve();
        return;
      }
      window.speechSynthesis.cancel();
      const utter = new SpeechSynthesisUtterance(text);
      utter.lang = this.pickVoice()?.lang || navigator.language || "pt-BR";
      const voice = this.pickVoice();
      if (voice) utter.voice = voice;
      utter.rate = 1.0;
      utter.pitch = 1.0;
      this.currentUtter = utter;

      utter.onend = () => {
        if (gen !== this.generation) return;
        this.currentUtter = null;
        this.playing = false;
        this.pump(gen);
        resolve();
      };
      utter.onerror = () => {
        if (gen !== this.generation) return;
        this.currentUtter = null;
        this.playing = false;
        this.pump(gen);
        resolve();
      };

      window.speechSynthesis.speak(utter);
    });
  }

  pickVoice() {
    const voices = window.speechSynthesis.getVoices();
    let pool = voices.filter((v) => v.lang && v.lang.toLowerCase().startsWith("pt"));
    if (!pool.length) pool = voices;
    if (!pool.length) return null;
    const score = (v) => {
      const name = (v.name || "").toLowerCase();
      let s = 0;
      if (name.includes("natural")) s += 10;
      if (name.includes("neural")) s += 8;
      if (name.includes("online")) s += 8;
      if (name.includes("electra")) s -= 6;
      if (name.includes("zira") || name.includes("david")) s -= 4;
      return s;
    };
    return [...pool].sort((a, b) => score(b) - score(a))[0];
  }
}

if (typeof window !== "undefined") window.SpeechManager = SpeechManager;
