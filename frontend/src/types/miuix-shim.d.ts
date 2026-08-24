/**
 * Type shims for miuix-vue 0.1.x whose package.json "types" entry is broken
 * (points at a non-existent file). Real .d.ts files live under dist/src/.
 */
declare module "miuix-vue" {
  import type { DefineComponent } from "vue";

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  type Comp<P = any> = DefineComponent<P, any, any>;

  export const MiuixButton: Comp<{
    type?: "default" | "primary";
    disabled?: boolean;
  }>;
  export type MiuixButtonType = "default" | "primary";
  export const MiuixCard: Comp<{
    pressFeedback?: boolean | string;
    showIndication?: boolean;
    holdDown?: boolean;
  }>;
  export type MiuixCardPressFeedback = boolean | string;
  export const MiuixDialog: Comp<{
    modelValue?: boolean;
    title?: string;
    summary?: string;
    closeOnClickModal?: boolean;
  }>;
  export const MiuixInput: Comp<{
    modelValue?: string;
    label?: string;
    useLabelAsPlaceholder?: boolean;
    placeholder?: string;
    disabled?: boolean;
    readonly?: boolean;
    singleLine?: boolean;
  }>;
  export const MiuixSlider: Comp<{ modelValue?: number; min?: number; max?: number }>;
  export const MiuixRangeSlider: Comp<Record<string, never>>;
  export const MiuixSwitch: Comp<{ modelValue?: boolean }>;
  export const MiuixText: Comp<{
    type?:
      | "main"
      | "title1"
      | "title2"
      | "title3"
      | "title4"
      | "subtitle"
      | "body1"
      | "body2"
      | "footnote1"
      | "footnote2"
      | "paragraph"
      | "button";
    color?: string;
    size?: number | string;
    weight?: number | string;
    align?: "start" | "center" | "end" | "justify";
    as?: string;
  }>;
  export type MiuixTextType = InstanceType<typeof MiuixText> extends never
    ? never
    : Parameters<typeof Object>[0];
  export type MiuixTextWeight = number | string;
  export const MiuixSmallTitle: Comp<{ title?: string }>;
  export const MiuixDivider: Comp<Record<string, never>>;
  export const MiuixSurface: Comp<Record<string, never>>;
  export const MiuixBasicComponent: Comp<Record<string, never>>;
  export const MiuixIcon: Comp<{ value?: string; size?: number | string }>;
  export const MiuixIconButton: Comp<{
    disabled?: boolean;
    /** icon glyph name rendered by the built-in icon font */
  }>;
  export const MiuixCheckbox: Comp<{ modelValue?: boolean; disabled?: boolean }>;
  export const MiuixRadioButton: Comp<Record<string, never>>;
  export const MiuixSwitchPreference: Comp<Record<string, never>>;
  export const MiuixArrowPreference: Comp<Record<string, never>>;
  export const MiuixCheckboxPreference: Comp<Record<string, never>>;
  export const MiuixRadioButtonPreference: Comp<Record<string, never>>;
  export const MiuixTabRow: Comp<{
    modelValue?: number;
    tabs?: string[];
    contour?: boolean;
  }>;
  export const MiuixProgressIndicator: Comp<Record<string, never>>;
  export const MiuixDropdownPreference: Comp<Record<string, never>>;
  export interface MiuixDropdownItem {
    label?: string;
    [k: string]: unknown;
  }
  export const MiuixSpinnerPreference: Comp<Record<string, never>>;
  export const MiuixSnackbarHost: Comp<Record<string, never>>;
  export function showSnackbar(o?: Record<string, unknown>): unknown;
  export function dismissSnackbar(): void;
  export function dismissNewestSnackbar(): void;
  export function dismissOldestSnackbar(): void;
  export type SnackbarOptions = Record<string, unknown>;
  export type SnackbarResult = unknown;
  export type SnackbarDuration = number;
  export const MiuixSearchBar: Comp<{
    modelValue?: string;
    expanded?: boolean;
    label?: string;
    cancelText?: string;
  }>;
  export const MiuixNumberPicker: Comp<Record<string, never>>;
  export const MiuixColorPicker: Comp<Record<string, never>>;
  export const MiuixFloatingActionButton: Comp<Record<string, never>>;
  export const MiuixTopAppBar: Comp<{
    title?: string;
    largeTitle?: string;
    subtitle?: string;
    large?: boolean;
    color?: string;
  }>;
  export const MiuixNavigationBar: Comp<{
    modelValue?: number;
    items?: { label: string }[];
    showDivider?: boolean;
  }>;
  export interface MiuixNavigationItem {
    label: string;
  }
  export const MiuixBottomSheet: Comp<Record<string, never>>;
  export const MiuixScrollArea: Comp<Record<string, never>>;

  export const IconArrowRight: unknown;
  export const IconArrowUpDown: unknown;
  export const IconCheck: unknown;
  export const IconSearch: unknown;

  export const version: string;

  export type Theme = "light" | "dark";
  export type ThemeMode = "system" | "light" | "dark";
  export function setThemeMode(next: ThemeMode): void;
  export function setTheme(next: Theme): void;
  export function useTheme(): {
    theme: { value: Theme };
    mode: { value: ThemeMode };
    setTheme: (t: Theme) => void;
    setThemeMode: (m: ThemeMode) => void;
  };

  export function folmeSpring(...args: unknown[]): unknown;
  export function folmeSpringByResponse(...args: unknown[]): unknown;
  export function accelerateEasing(...args: unknown[]): unknown;
  export function decelerateEasing(...args: unknown[]): unknown;
  export function sinOutEasing(...args: unknown[]): unknown;
  export type EasingFn = (...args: number[]) => number;
}
