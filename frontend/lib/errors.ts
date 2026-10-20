import { toast } from "sonner";
import { ApiError } from "./api";

export function toastError(err: unknown) {
  if (err instanceof ApiError) {
    toast.error(err.message, { description: err.status === 412 ? "Preconditions changed — review the latest state." : undefined });
  } else {
    toast.error(err instanceof Error ? err.message : "Action failed");
  }
}
