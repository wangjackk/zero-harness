package routine

import "testing"

func TestTerminationReasonFromWire(t *testing.T) {
	for _, reason := range []TerminationReason{
		ReasonCompleted, ReasonStopped, ReasonPreempted, ReasonFailed,
		ReasonCancelled, ReasonDestroyed, ReasonDisconnected, ReasonUnknown,
	} {
		if got := TerminationReasonFromWire(string(reason)); got != reason {
			t.Fatalf("round trip %q: got %q", reason, got)
		}
	}
	if got := TerminationReasonFromWire("legacy"); got != ReasonUnknown {
		t.Fatalf("unknown wire value: got %q", got)
	}
	if got := TerminationReasonFromWire(nil); got != ReasonUnknown {
		t.Fatalf("missing wire value: got %q", got)
	}
}

func TestHandleNotifyDoneCarriesTerminationMetadata(t *testing.T) {
	h := newRoutineHandle("child", "worker", nil, nil)
	h.NotifyDone(nil, "", ReasonPreempted, "evictor", "evicted by evictor")
	if h.Reason != ReasonPreempted || h.By != "evictor" || h.Detail != "evicted by evictor" {
		t.Fatalf("termination metadata mismatch: reason=%q by=%q detail=%q", h.Reason, h.By, h.Detail)
	}
	result, err := h.Wait()
	if err != nil || result != nil {
		t.Fatalf("PREEMPTED must preserve wait policy: result=%v err=%v", result, err)
	}
}
