// useAiChat — 9.10 AI scratchpad (v3.66.381+).
//
// One stateless "Ask the model" turn over the hardened LLM execution contract.
// The backend (POST /api/ai/chat) respects the AI master switch, fails open on
// provider errors (always HTTP 200), enforces prompt length / vision-model-for-
// image / timeout, and persists NOTHING. This hook is a thin mutation wrapper:
// it owns an AbortController so the caller can cancel an in-flight turn.
//
// FULL /api/ literal (scanner credit — gui_parity_inventory flips it spa_wired):
//   POST /api/ai/chat   body {prompt, model?, system?, image_b64?}
// Rides apiPost (X-CSRF-Token + JSON + 403-retry), never a raw fetch().

import * as React from "react";

import { useMutation } from "@tanstack/react-query";

import { apiPost } from "@/lib/api-client";
import type { AiChatRequest, AiChatResponse } from "@/lib/api-types";

export function useAiChat() {
  const controllerRef = React.useRef<AbortController | null>(null);

  const mutation = useMutation<AiChatResponse, Error, AiChatRequest>({
    mutationFn: (req) => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;
      return apiPost<AiChatResponse>("/api/ai/chat", req, controller.signal);
    },
  });

  const cancel = React.useCallback(() => {
    controllerRef.current?.abort();
    mutation.reset();
  }, [mutation]);

  return { ...mutation, cancel };
}
