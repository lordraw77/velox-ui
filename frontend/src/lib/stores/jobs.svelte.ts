/**
 * Model downloads and creations, as seen from the interface.
 *
 * The jobs run on the server (ADR-0017). This store only mirrors them: it lists what
 * is running, subscribes to each job's progress stream while the models page is open,
 * and lets go when it closes. Nothing is cancelled by closing the page — that is the
 * point of running the download on the server.
 */

import { api, VeloxApiError } from "$lib/api/client";
import { readSse } from "$lib/api/sse";
import type { JobSnapshot } from "$lib/api/types";
import { app } from "./app.svelte";

class JobsStore {
  jobs = $state<JobSnapshot[]>([]);

  /** Called once for every job that finishes while it is being followed. */
  onFinished: ((job: JobSnapshot) => void) | null = null;

  #followers = new Map<string, AbortController>();

  /** Load the job list and follow whatever is still running. */
  async refresh(): Promise<void> {
    try {
      const { jobs } = await api.jobs();
      this.jobs = jobs;
      for (const job of jobs) if (job.state === "running") void this.#follow(job.id);
    } catch (error) {
      app.report(error);
    }
  }

  async pull(providerId: string, name: string): Promise<void> {
    await this.#start(() => api.startPull(providerId, name));
  }

  async create(providerId: string, name: string, modelfile: string): Promise<void> {
    await this.#start(() => api.startCreate(providerId, name, modelfile));
  }

  async cancel(id: string): Promise<void> {
    try {
      await api.cancelJob(id);
    } catch (error) {
      // Finishing between the click and the request is not worth an error banner.
      if (!(error instanceof VeloxApiError && error.status === 404)) app.report(error);
    }
  }

  /** Stop following every job. The jobs themselves keep running on the server. */
  stopFollowing(): void {
    for (const controller of this.#followers.values()) controller.abort();
    this.#followers.clear();
  }

  async #start(request: () => Promise<JobSnapshot>): Promise<void> {
    try {
      const job = await request();
      this.#upsert(job);
      void this.#follow(job.id);
    } catch (error) {
      app.report(error);
    }
  }

  #upsert(snapshot: JobSnapshot): void {
    const index = this.jobs.findIndex((job) => job.id === snapshot.id);
    if (index === -1) this.jobs = [snapshot, ...this.jobs];
    else this.jobs[index] = snapshot;
  }

  async #follow(id: string): Promise<void> {
    if (this.#followers.has(id)) return;
    const controller = new AbortController();
    this.#followers.set(id, controller);
    try {
      const response = await api.followJob(id, controller.signal);
      for await (const event of readSse(response.body!, controller.signal)) {
        const snapshot = JSON.parse(event.data) as JobSnapshot;
        this.#upsert(snapshot);
        if (event.event === "done") {
          this.onFinished?.(snapshot);
          break;
        }
      }
    } catch (error) {
      if ((error as Error).name !== "AbortError") app.report(error);
    } finally {
      this.#followers.delete(id);
    }
  }
}

export const jobs = new JobsStore();
