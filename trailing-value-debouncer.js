'use strict';

class TrailingValueDebouncer {
  constructor(delayMs, applyValue) {
    this.delayMs = delayMs;
    this.applyValue = applyValue;
    this.pending = null;
    this.applying = false;
  }

  schedule(value) {
    if (this.pending) {
      clearTimeout(this.pending.timer);
      this.pending.resolve({ applied: false, value: this.pending.value });
    }

    return new Promise((resolve, reject) => {
      this.pending = { value, resolve, reject, timer: null };
      if (!this.applying) this.arm();
    });
  }

  arm() {
    if (!this.pending || this.applying) return;
    const scheduled = this.pending;
    scheduled.timer = setTimeout(() => this.flush(scheduled), this.delayMs);
  }

  async flush(scheduled) {
    if (this.pending !== scheduled || this.applying) return;
    this.pending = null;
    this.applying = true;
    try {
      await this.applyValue(scheduled.value);
      scheduled.resolve({ applied: true, value: scheduled.value });
    } catch (error) {
      scheduled.reject(error);
    } finally {
      this.applying = false;
      this.arm();
    }
  }

  cancel() {
    if (!this.pending) return;
    clearTimeout(this.pending.timer);
    this.pending.resolve({ applied: false, value: this.pending.value });
    this.pending = null;
  }
}

module.exports = { TrailingValueDebouncer };
