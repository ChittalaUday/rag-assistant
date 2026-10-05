import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

// Local equivalent of Animate UI's @workspace/ui/lib/utils dependency.
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
