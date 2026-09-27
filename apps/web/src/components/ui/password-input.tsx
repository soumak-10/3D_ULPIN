"use client";

import { Eye, EyeOff } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";
import { Input, type InputProps } from "./input";

/**
 * A password field with a reveal toggle.
 *
 * Typing a 12-character password with a symbol into a field you cannot read is
 * how people end up locked out, so the toggle is worth the extra markup. Two
 * details matter and are easy to get wrong:
 *
 *   - the toggle is `type="button"`. A bare `<button>` inside a form defaults to
 *     `submit`, so omitting this would post the form on every reveal.
 *   - the visible state lives here, not in the parent. The input's `value` is
 *     still owned by whoever passes it, so this drops into a react-hook-form
 *     `register()` spread unchanged.
 *
 * The ref is forwarded to the input itself, not the wrapper, so `register`'s
 * ref and `setFocus` land on the control.
 */
const PasswordInput = React.forwardRef<HTMLInputElement, Omit<InputProps, "type">>(
  ({ className, ...props }, ref) => {
    const [visible, setVisible] = React.useState(false);

    return (
      <div className="relative">
        <Input
          ref={ref}
          type={visible ? "text" : "password"}
          // Room for the toggle, so a long password does not run underneath it.
          className={cn("pr-10", className)}
          {...props}
        />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          // The label carries the meaning; the icon is decorative. `aria-pressed`
          // lets a screen reader announce the current state rather than just the
          // action.
          aria-label={visible ? "Hide password" : "Show password"}
          aria-pressed={visible}
          disabled={props.disabled}
          className="absolute inset-y-0 right-0 flex w-10 items-center justify-center rounded-r-md text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {visible ? (
            <EyeOff className="size-4" aria-hidden />
          ) : (
            <Eye className="size-4" aria-hidden />
          )}
        </button>
      </div>
    );
  },
);
PasswordInput.displayName = "PasswordInput";

export { PasswordInput };
