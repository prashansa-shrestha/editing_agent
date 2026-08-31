"use strict";

export class StaleViewerLoadError extends Error {
  constructor() {
    super("viewer load generation is stale");
    this.name = "StaleViewerLoadError";
  }
}

export class ViewerLoadLifecycle {
  constructor() {
    this.generation = 0;
    this.state = "idle";
  }

  begin() {
    this.generation += 1;
    this.state = "loading";
    return this.generation;
  }

  isCurrent(generation) {
    return generation === this.generation;
  }

  requireCurrent(generation) {
    if (!this.isCurrent(generation)) {
      throw new StaleViewerLoadError();
    }
  }

  ready(generation) {
    this.requireCurrent(generation);
    if (this.state !== "loading") {
      throw new Error("viewer can become ready only from loading");
    }
    this.state = "ready";
  }

  fail(generation) {
    this.requireCurrent(generation);
    if (this.state !== "loading") {
      throw new Error("viewer can fail only from loading");
    }
    this.state = "error";
  }

  cancel() {
    this.generation += 1;
    this.state = "idle";
  }
}
