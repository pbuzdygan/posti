import {
  ChangeEvent,
  FocusEvent,
  FormEvent,
  MouseEvent as ReactMouseEvent,
  useEffect,
  useMemo,
  useRef,
  useState
} from "react";
import { buildScript, extractProfilesFromScript, SerializedProfiles, SerializedStep } from "./postiTemplate";
import "./styles.css";

type Step = {
  id: string;
  title: string;
  description: string;
  command: string;
  confirm: boolean;
  enabled: boolean;
};

type Profile = {
  label: string;
  description: string;
  preflight: string[];
  steps: Step[];
};

type ProfileMap = Record<string, Profile>;
type StatusLevel = "info" | "success" | "warning" | "error";

type ProfileModalResult = {
  label: string;
  description: string;
  preflight: string[];
};

type FileHandle =
  | {
      kind?: string;
      name?: string;
      getFile?: () => Promise<File>;
      createWritable?: () => Promise<{
        write(data: Blob | BufferSource | string): Promise<void>;
        close(): Promise<void>;
      }>;
    }
  | null;

const emptyStepForm = { title: "", description: "", command: "", confirm: false };

const createStepId = () => {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `step-${Math.random().toString(36).slice(2, 11)}`;
};

const DEFAULT_VERSION = "1.0";

const createStep = (config: {
  title: string;
  command: string;
  description?: string;
  confirm?: boolean;
  enabled?: boolean;
}): Step => ({
  id: createStepId(),
  title: config.title,
  command: config.command,
  description: config.description ?? "",
  confirm: Boolean(config.confirm),
  enabled: config.enabled ?? true
});

const pythonKeywords = new Set([
  "False",
  "None",
  "True",
  "and",
  "as",
  "assert",
  "async",
  "await",
  "break",
  "class",
  "continue",
  "def",
  "del",
  "elif",
  "else",
  "except",
  "finally",
  "for",
  "from",
  "global",
  "if",
  "import",
  "in",
  "is",
  "lambda",
  "nonlocal",
  "not",
  "or",
  "pass",
  "raise",
  "return",
  "try",
  "while",
  "with",
  "yield"
]);

const pythonBuiltins = new Set([
  "abs",
  "all",
  "any",
  "ascii",
  "bin",
  "bool",
  "bytes",
  "callable",
  "chr",
  "dict",
  "enumerate",
  "len",
  "list",
  "max",
  "min",
  "print",
  "range",
  "repr",
  "set",
  "sorted",
  "str",
  "sum",
  "tuple",
  "type",
  "zip"
]);

const stringPrefixChars = new Set(["r", "R", "u", "U", "b", "B", "f", "F"]);
const punctuationChars = new Set(["(", ")", "[", "]", "{", "}", ",", ".", ";", "?", "\\", "'", '"', ":"]);
const operatorMatchers = [
  "**=",
  "//=",
  ">>=",
  "<<=",
  "+=",
  "-=",
  "*=",
  "/=",
  "%=",
  "&=",
  "|=",
  "^=",
  "->",
  "==",
  "!=",
  "<=",
  ">=",
  "//",
  "**",
  ">>",
  "<<",
  ":=",
  "+",
  "-",
  "*",
  "/",
  "%",
  "=",
  "!",
  "<",
  ">",
  "&",
  "|",
  "^",
  "~"
].sort((a, b) => b.length - a.length);

type PythonTokenType =
  | "plain"
  | "keyword"
  | "builtin"
  | "string"
  | "comment"
  | "number"
  | "function"
  | "class"
  | "decorator"
  | "operator"
  | "punctuation"
  | "self";

type PythonToken = { type: PythonTokenType; value: string };

const escapeHtml = (text: string) =>
  text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

const isIdentifierStart = (char: string) => /[A-Za-z_]/.test(char);
const isIdentifierPart = (char: string) => /[A-Za-z0-9_]/.test(char);

const readPythonString = (source: string, start: number): { value: string; end: number } | null => {
  const length = source.length;
  let index = start;
  let prefixEnd = index;
  while (prefixEnd < length && stringPrefixChars.has(source[prefixEnd])) {
    prefixEnd += 1;
  }
  let quoteIndex = prefixEnd;
  let quoteChar = source[quoteIndex];
  if (quoteChar !== '"' && quoteChar !== "'") {
    if (source[index] !== '"' && source[index] !== "'") {
      return null;
    }
    quoteIndex = index;
    quoteChar = source[index];
    prefixEnd = index;
  }
  const triple =
    quoteIndex + 2 < length &&
    source[quoteIndex + 1] === quoteChar &&
    source[quoteIndex + 2] === quoteChar;
  let end = triple ? quoteIndex + 3 : quoteIndex + 1;
  while (end < length) {
    if (source[end] === "\\" && end + 1 < length) {
      end += 2;
      continue;
    }
    if (triple) {
      if (
        source[end] === quoteChar &&
        source[end + 1] === quoteChar &&
        source[end + 2] === quoteChar
      ) {
        end += 3;
        break;
      }
      end += 1;
      continue;
    }
    if (source[end] === quoteChar) {
      end += 1;
      break;
    }
    end += 1;
  }
  if (end > length) {
    end = length;
  }
  return { value: source.slice(start, end), end };
};

const readPythonNumber = (source: string, start: number): { value: string; end: number } | null => {
  const length = source.length;
  let index = start;
  const first = source[index];
  if (!/\d/.test(first) && !(first === "." && /\d/.test(source[index + 1]))) {
    return null;
  }

  const begin = index;
  if (first === "0" && index + 1 < length && /[boxBOX]/.test(source[index + 1])) {
    index += 2;
    while (index < length && /[0-9a-fA-F_]/.test(source[index])) {
      index += 1;
    }
    return { value: source.slice(begin, index), end: index };
  }

  while (index < length && /[\d_]/.test(source[index])) {
    index += 1;
  }

  if (source[index] === "." && /\d/.test(source[index + 1])) {
    index += 1;
    while (index < length && /[\d_]/.test(source[index])) {
      index += 1;
    }
  }

  if (/[eE]/.test(source[index])) {
    index += 1;
    if (/[+-]/.test(source[index])) {
      index += 1;
    }
    while (index < length && /[\d_]/.test(source[index])) {
      index += 1;
    }
  }

  if (/[jJ]/.test(source[index])) {
    index += 1;
  }

  return { value: source.slice(begin, index), end: index };
};

const readPythonOperator = (source: string, start: number): { value: string; end: number } | null => {
  for (const op of operatorMatchers) {
    if (source.startsWith(op, start)) {
      return { value: op, end: start + op.length };
    }
  }
  return null;
};

const tokenizePython = (code: string): PythonToken[] => {
  const tokens: PythonToken[] = [];
  const length = code.length;
  let index = 0;
  let pendingIdentifier: "function" | "class" | null = null;

  const pushToken = (type: PythonTokenType, value: string) => {
    if (value) {
      tokens.push({ type, value });
    }
  };

  while (index < length) {
    const char = code[index];

    if (char === " " || char === "\t") {
      let end = index + 1;
      while (end < length && (code[end] === " " || code[end] === "\t")) {
        end += 1;
      }
      pushToken("plain", code.slice(index, end));
      index = end;
      continue;
    }

    if (char === "\r" || char === "\n") {
      if (char === "\r" && code[index + 1] === "\n") {
        pushToken("plain", "\r\n");
        index += 2;
      } else {
        pushToken("plain", char);
        index += 1;
      }
      continue;
    }

    if (char === "@") {
      let end = index + 1;
      while (end < length && /[A-Za-z0-9_.]/.test(code[end])) {
        end += 1;
      }
      pushToken("decorator", code.slice(index, end));
      index = end;
      continue;
    }

    if (char === "#") {
      const newline = code.indexOf("\n", index);
      const end = newline === -1 ? length : newline;
      pushToken("comment", code.slice(index, end));
      index = end;
      continue;
    }

    if (char === '"' || char === "'" || stringPrefixChars.has(char)) {
      const result = readPythonString(code, index);
      if (result) {
        pushToken("string", result.value);
        index = result.end;
        continue;
      }
    }

    const numberToken = readPythonNumber(code, index);
    if (numberToken) {
      pushToken("number", numberToken.value);
      index = numberToken.end;
      continue;
    }

    if (isIdentifierStart(char)) {
      let end = index + 1;
      while (end < length && isIdentifierPart(code[end])) {
        end += 1;
      }
      const word = code.slice(index, end);
      let type: PythonTokenType = "plain";
      if (pythonKeywords.has(word)) {
        type = "keyword";
        if (word === "def") {
          pendingIdentifier = "function";
        } else if (word === "class") {
          pendingIdentifier = "class";
        } else {
          pendingIdentifier = null;
        }
      } else if (pendingIdentifier) {
        type = pendingIdentifier;
        pendingIdentifier = null;
      } else if (pythonBuiltins.has(word)) {
        type = "builtin";
      } else if (word === "self") {
        type = "self";
      }
      pushToken(type, word);
      index = end;
      continue;
    }

    const opToken = readPythonOperator(code, index);
    if (opToken) {
      pushToken("operator", opToken.value);
      index = opToken.end;
      continue;
    }

    if (punctuationChars.has(char)) {
      pushToken("punctuation", char);
      index += 1;
      continue;
    }

    pushToken("plain", char);
    index += 1;
  }

  return tokens;
};

const highlightPython = (code: string) =>
  tokenizePython(code)
    .map((token) =>
      token.type === "plain"
        ? escapeHtml(token.value)
        : `<span class="token ${token.type}">${escapeHtml(token.value)}</span>`
    )
    .join("");

const stripExtension = (name: string) => name.replace(/\.[^.]+$/, "");
const extractBaseName = (name: string | null | undefined) => {
  if (!name) {
    return "posti";
  }
  const stripped = stripExtension(name.trim());
  const match = stripped.match(/^(.*)_v\d+(?:\.\d+)?$/i);
  return (match && match[1] ? match[1] : stripped) || "posti";
};

const bumpVersion = (version: string) => {
  const [majorRaw = "1", minorRaw = "0"] = version.split(".");
  const major = Number.parseInt(majorRaw, 10);
  const minor = Number.parseInt(minorRaw, 10);
  if (Number.isNaN(major) || Number.isNaN(minor)) {
    return DEFAULT_VERSION;
  }
  return `${major}.${minor + 1}`;
};

const parseContentDispositionFilename = (header: string | null, fallback: string) => {
  if (!header) {
    return fallback;
  }
  const match = header.match(/filename\*?=(?:UTF-8'')?("?)([^";]+)\1/);
  if (match && match[2]) {
    try {
      return decodeURIComponent(match[2]);
    } catch {
      return match[2];
    }
  }
  return fallback;
};

const downloadBlob = (blob: Blob, filename: string) => {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
};

const handlePlaceholderFocus = (event: FocusEvent<HTMLInputElement | HTMLTextAreaElement>) => {
  const target = event.currentTarget;
  if (!target.dataset.placeholder) {
    target.dataset.placeholder = target.placeholder;
  }
  target.placeholder = "";
};

const handlePlaceholderBlur = (event: FocusEvent<HTMLInputElement | HTMLTextAreaElement>) => {
  const target = event.currentTarget;
  target.placeholder = target.dataset.placeholder || "";
};

const slugify = (text: string, taken: Set<string>): string => {
  const base = text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "") || "profile";
  let candidate = base;
  let counter = 1;
  while (taken.has(candidate)) {
    candidate = `${base}-${counter}`;
    counter += 1;
  }
  return candidate;
};

type BeforeInstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;
};

const App = () => {
  const [profiles, setProfiles] = useState<ProfileMap>({});
  const [profileOrder, setProfileOrder] = useState<string[]>([]);
  const [activeProfileKey, setActiveProfileKey] = useState<string>("");
  const [stepForm, setStepForm] = useState(emptyStepForm);
  const [activeStepId, setActiveStepId] = useState<string | null>(null);
  const [selectedStepIds, setSelectedStepIds] = useState<Set<string>>(new Set());
  const [selectionAnchor, setSelectionAnchor] = useState<number | null>(null);
  const [preview, setPreview] = useState("");
  const [status, setStatus] = useState<{ message: string; level: StatusLevel } | null>(null);
  const [currentFileName, setCurrentFileName] = useState("No file loaded");
  const [profileModalState, setProfileModalState] = useState<null | { mode: "add" | "edit" }>(null);
  const [pendingDelete, setPendingDelete] = useState(false);
  const [pendingNewProject, setPendingNewProject] = useState(false);
  const [loadedFileName, setLoadedFileName] = useState<string | null>(null);
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);
  const [projectVersion, setProjectVersion] = useState(DEFAULT_VERSION);
  const [isBuildingBinary, setIsBuildingBinary] = useState(false);
  const [fileHandle, setFileHandle] = useState<FileHandle>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const profileSelectRef = useRef<HTMLDivElement | null>(null);
  const [isProfileMenuOpen, setIsProfileMenuOpen] = useState(false);
  const [installPromptEvent, setInstallPromptEvent] = useState<BeforeInstallPromptEvent | null>(null);
  const [installBannerDismissed, setInstallBannerDismissed] = useState(false);
  const [pwaSupportHint, setPwaSupportHint] = useState<string | null>(null);
  const isSaveMode = Boolean(hasUnsavedChanges);
  const builderBaseUrl =
    (import.meta.env.VITE_BUILDER_URL ? import.meta.env.VITE_BUILDER_URL.trim() : "/api").replace(/\/$/, "");
  const highlightedPreview = useMemo(() => (preview ? highlightPython(preview) : ""), [preview]);
  const getBaseFileName = () => extractBaseName(loadedFileName || fileHandle?.name || "posti");

  const markDirty = () => {
    setHasUnsavedChanges(true);
    setPendingNewProject(false);
    setPendingDelete(false);
  };

  const currentProfile = profiles[activeProfileKey];
  const stepCount = currentProfile?.steps.length ?? 0;

  useEffect(() => {
    if (!currentProfile) {
      return;
    }
    setSelectedStepIds((prev) => {
      const valid = new Set(currentProfile.steps.map((step) => step.id));
      const filtered = Array.from(prev).filter((id) => valid.has(id));
      return filtered.length === prev.size ? prev : new Set(filtered);
    });
  }, [currentProfile]);

  useEffect(() => {
    setIsProfileMenuOpen(false);
  }, [profileOrder]);

  useEffect(() => {
    if (!status) {
      return;
    }
    const timer = window.setTimeout(() => setStatus(null), 3200);
    return () => window.clearTimeout(timer);
  }, [status]);

  useEffect(() => {
    const handleBeforeInstallPrompt = (event: Event) => {
      event.preventDefault();
      setInstallPromptEvent(event as BeforeInstallPromptEvent);
      setInstallBannerDismissed(false);
      setPwaSupportHint(null);
    };
    const handleAppInstalled = () => {
      setInstallPromptEvent(null);
      setInstallBannerDismissed(true);
    };
    window.addEventListener("beforeinstallprompt", handleBeforeInstallPrompt);
    window.addEventListener("appinstalled", handleAppInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", handleBeforeInstallPrompt);
      window.removeEventListener("appinstalled", handleAppInstalled);
    };
  }, []);


  useEffect(() => {
    const handleClick = (event: MouseEvent) => {
      if (profileSelectRef.current && !profileSelectRef.current.contains(event.target as Node)) {
        setIsProfileMenuOpen(false);
      }
    };
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setIsProfileMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, []);

  useEffect(() => {
    setSelectedStepIds(new Set());
    setActiveStepId(null);
    setStepForm(emptyStepForm);
    setSelectionAnchor(null);
    setPendingDelete(false);
    setIsProfileMenuOpen(false);
  }, [activeProfileKey]);

  useEffect(() => {
    if (!currentProfile || !activeStepId) {
      return;
    }
    const step = currentProfile.steps.find((item) => item.id === activeStepId);
    if (step) {
      setStepForm({
        title: step.title,
        description: step.description,
        command: step.command,
        confirm: step.confirm
      });
    }
  }, [activeStepId, currentProfile]);

  const flash = (message: string, level: StatusLevel = "info") => {
    setStatus({ message, level });
  };

  const saveScriptToServer = async (
    content: string,
    versionLabel: string,
    baseName: string
  ): Promise<{ blob: Blob; filename: string }> => {
    const response = await fetch(`${builderBaseUrl}/save-script`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ script: content, version: versionLabel, filename: baseName })
    });
    if (!response.ok) {
      const detail = await response.text().catch(() => "");
      throw new Error(detail || `Status ${response.status}`);
    }
    const blob = await response.blob();
    const filename = response.headers.get("X-Posti-Filename") ?? `${baseName}_v${versionLabel}.py`;
    return { blob, filename };
  };

  useEffect(() => {
    const className = `theme-${theme}`;
    document.body.classList.remove("theme-dark", "theme-light");
    document.body.classList.add(className);
    return () => {
      document.body.classList.remove(className);
    };
  }, [theme]);

  const projectEmpty = profileOrder.length === 0;
  const hasProfiles = profileOrder.length > 0;

  const updateCurrentProfile = (updater: (profile: Profile) => Profile) => {
    if (!currentProfile) {
      return;
    }
    setProfiles((prev) => {
      const next = { ...prev };
      const profile = next[activeProfileKey];
      if (!profile) {
        return prev;
      }
      next[activeProfileKey] = updater(profile);
      return next;
    });
    markDirty();
  };

  const handleAddStep = () => {
    if (!currentProfile) {
      flash("Add a profile first.", "warning");
      return;
    }
    if (!stepForm.command.trim()) {
      flash("Command cannot be empty.", "warning");
      return;
    }
    const title = stepForm.title.trim() || `Step ${currentProfile.steps.length + 1}`;
    const newStep = createStep({
      title,
      command: stepForm.command.trim(),
      description: stepForm.description.trim(),
      confirm: stepForm.confirm
    });
    updateCurrentProfile((profile) => ({
      ...profile,
      steps: [...profile.steps, newStep]
    }));
    setStepForm(emptyStepForm);
    setActiveStepId(null);
    setSelectedStepIds(new Set());
    setSelectionAnchor(null);
    flash(`Step "${title}" added.`, "success");
  };

  const handleUpdateStep = () => {
    if (!currentProfile || !activeStepId) {
      return;
    }
    if (!stepForm.command.trim()) {
      flash("Command cannot be empty.", "warning");
      return;
    }
    const title = stepForm.title.trim() || "Untitled step";
    updateCurrentProfile((profile) => ({
      ...profile,
      steps: profile.steps.map((step) =>
        step.id === activeStepId
          ? {
              ...step,
              title,
              description: stepForm.description.trim(),
              command: stepForm.command.trim(),
              confirm: stepForm.confirm
            }
          : step
      )
    }));
    flash(`Step "${title}" updated.`, "success");
  };

  const handleRemoveStep = () => {
    if (!currentProfile || !activeStepId) {
      return;
    }
    updateCurrentProfile((profile) => ({
      ...profile,
      steps: profile.steps.filter((step) => step.id !== activeStepId)
    }));
    setActiveStepId(null);
    setStepForm(emptyStepForm);
    flash("Step removed.", "info");
  };

  const handleMoveStep = (delta: number) => {
    if (!currentProfile || !activeStepId) {
      return;
    }
    const index = currentProfile.steps.findIndex((step) => step.id === activeStepId);
    const target = index + delta;
    if (index === -1 || target < 0 || target >= currentProfile.steps.length) {
      return;
    }
    updateCurrentProfile((profile) => {
      const clone = [...profile.steps];
      const [removed] = clone.splice(index, 1);
      clone.splice(target, 0, removed);
      return { ...profile, steps: clone };
    });
  };

  const handleCloneStep = () => {
    if (!currentProfile || !activeStepId) {
      return;
    }
    const source = currentProfile.steps.find((step) => step.id === activeStepId);
    if (!source) {
      return;
    }
    const clone = createStep({
      title: `${source.title} (copy)`,
      command: source.command,
      description: source.description,
      confirm: source.confirm,
      enabled: source.enabled
    });
    const index = currentProfile.steps.findIndex((step) => step.id === activeStepId);
    updateCurrentProfile((profile) => {
      const copy = [...profile.steps];
      copy.splice(index + 1, 0, clone);
      return { ...profile, steps: copy };
    });
    setActiveStepId(clone.id);
    flash("Step cloned.", "success");
  };

  const handleConfirmToggle = () => {
    if (!currentProfile || !stepForm.command.trim()) {
      return;
    }
    const nextValue = !stepForm.confirm;
    setStepForm((prev) => ({ ...prev, confirm: nextValue }));
    if (activeStepId) {
      updateCurrentProfile((profile) => ({
        ...profile,
        steps: profile.steps.map((step) => (step.id === activeStepId ? { ...step, confirm: nextValue } : step))
      }));
    }
  };

  const handleBulkToggle = (enabled: boolean) => {
    if (!currentProfile || selectedStepIds.size === 0) {
      return;
    }
    const targetIds = selectedStepIds;
    updateCurrentProfile((profile) => ({
      ...profile,
      steps: profile.steps.map((step) => (targetIds.has(step.id) ? { ...step, enabled } : step))
    }));
    flash(
      `${enabled ? "Enabled" : "Disabled"} ${selectedStepIds.size} step${selectedStepIds.size === 1 ? "" : "s"}.`,
      enabled ? "success" : "warning"
    );
  };

  const handleGenerateScript = () => {
    if (!profileOrder.length) {
      flash("Add at least one profile to generate posti.py", "warning");
      return;
    }
    const script = buildScriptFromState();
    setPreview(script);
    flash("Preview updated.", "success");
  };

  const handleCopyPreview = async () => {
    if (!preview.trim()) {
      flash("Generate the script before copying.", "warning");
      return;
    }
    const copyText = preview;

    const attemptNativeCopy = async () => {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(copyText);
        return true;
      }
      return false;
    };

    const attemptFallbackCopy = () => {
      const textarea = document.createElement("textarea");
      textarea.value = copyText;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      textarea.style.pointerEvents = "none";
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      let success = false;
      try {
        success = document.execCommand("copy");
      } catch (error) {
        console.error(error);
        success = false;
      }
      textarea.remove();
      return success;
    };

    try {
      const nativeCopied = await attemptNativeCopy();
      if (nativeCopied) {
        flash("Script copied to clipboard.", "success");
        return;
      }
    } catch (error) {
      console.error(error);
    }

    if (attemptFallbackCopy()) {
      flash("Script copied to clipboard.", "success");
    } else {
      flash("Clipboard copy failed. Please copy manually.", "error");
    }
  };

  const buildPayload = (): SerializedProfiles => {
    const payload: SerializedProfiles = {};
    profileOrder.forEach((key) => {
      const profile = profiles[key];
      if (!profile) {
        return;
      }
      payload[key] = {
        label: profile.label,
        description: profile.description,
        preflight: profile.preflight,
        steps: profile.steps.map((step) => {
          const serialized: SerializedStep = {
            title: step.title,
            command: step.command,
            confirm: step.confirm,
            enabled: step.enabled
          };
          if (step.description) {
            serialized.description = step.description;
          }
          return serialized;
        })
      };
    });
    return payload;
  };

  const buildScriptFromState = (versionOverride?: string) =>
    buildScript(buildPayload(), versionOverride ?? projectVersion);

  const refreshPreviewVersion = (version: string) => {
    if (preview) {
      setPreview(buildScriptFromState(version));
    }
  };

  const handleSaveProject = async () => {
    if (!hasUnsavedChanges) {
      flash("No changes to save.", "info");
      return;
    }
    const baseName = getBaseFileName();
    const nextVersion = bumpVersion(projectVersion);
    const script = buildScriptFromState(nextVersion);
    const fallbackName = `${baseName}_v${nextVersion}.py`;
    try {
      const { blob, filename } = await saveScriptToServer(script, nextVersion, baseName);
      const saveName = filename || fallbackName;
      downloadBlob(blob, saveName);
      setLoadedFileName(saveName);
      setCurrentFileName(saveName);
      setFileHandle(null);
      setHasUnsavedChanges(false);
      setProjectVersion(nextVersion);
      refreshPreviewVersion(nextVersion);
      flash(`Saved ${saveName} and downloaded locally.`, "success");
    } catch (error) {
      console.error("Server persistence failed", error);
      downloadBlob(new Blob([script], { type: "text/x-python" }), fallbackName);
      flash("Server persistence failed. Downloaded locally only.", "warning");
    }
  };

  const handleProjectAction = async () => {
    if (isSaveMode) {
      await handleSaveProject();
    } else {
      await handleLoadProject();
    }
  };

  const buildBinaryUrl = `${builderBaseUrl}/build-binary`;

  const handleBuildBinary = async () => {
    if (!profileOrder.length) {
      flash("Add at least one profile before building a binary.", "warning");
      return;
    }
    const script = buildScriptFromState();
    const baseName = getBaseFileName();
    const versionLabel = projectVersion;
    setIsBuildingBinary(true);
    try {
      const response = await fetch(buildBinaryUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ script, filename: baseName, version: versionLabel })
      });
      if (!response.ok) {
        throw new Error(`Status ${response.status}`);
      }
      const blob = await response.blob();
      const artifactName =
        response.headers.get("X-Posti-Filename") ??
        parseContentDispositionFilename(response.headers.get("content-disposition"), `${baseName}_v${versionLabel}`);
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = artifactName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(link.href);
      flash(`Binary build completed (${artifactName}).`, "success");
    } catch (error) {
      console.error(error);
      flash("Binary build failed. Ensure the backend PyInstaller service is available.", "error");
    } finally {
      setIsBuildingBinary(false);
    }
  };

  const performResetProject = () => {
    setProfiles({});
    setProfileOrder([]);
    setActiveProfileKey("");
    setActiveStepId(null);
    setStepForm(emptyStepForm);
    setSelectedStepIds(new Set());
    setPreview("");
    setLoadedFileName(null);
    setCurrentFileName("No file loaded");
    setFileHandle(null);
    setHasUnsavedChanges(false);
    setProjectVersion(DEFAULT_VERSION);
    setPendingNewProject(false);
    setPendingDelete(false);
    flash("Blank project ready.", "success");
  };

  const handleNewProjectClick = () => {
    setPendingDelete(false);
    if (!pendingNewProject) {
      setPendingNewProject(true);
      flash("You are starting a new project. Press confirm to continue.", "warning");
      return;
    }
    performResetProject();
  };

  const importProjectFromFile = async (file: File, handle: FileHandle) => {
    const text = await file.text();
    const extracted = extractProfilesFromScript(text);
    if (!extracted) {
      flash("Could not find embedded profile data.", "error");
      return false;
    }
    const nextProfiles: ProfileMap = {};
    extracted.order.forEach((key) => {
      const payload = extracted.payload[key];
      if (!payload) {
        return;
      }
      nextProfiles[key] = {
        label: payload.label || key,
        description: payload.description || "",
        preflight: Array.isArray(payload.preflight) ? payload.preflight : [],
        steps: (payload.steps || []).map((step, index) => ({
          id: createStepId(),
          title: step.title || `Step ${index + 1}`,
          command: step.command || "",
          description: step.description ?? "",
          confirm: Boolean(step.confirm),
          enabled: step.enabled !== false
        }))
      };
    });
    setProfiles(nextProfiles);
    setProfileOrder(extracted.order);
    setActiveProfileKey(extracted.order[0] ?? "");
    setActiveStepId(null);
    setStepForm(emptyStepForm);
    setSelectedStepIds(new Set());
    setPreview("");
    setCurrentFileName(file.name);
    setLoadedFileName(file.name);
    setFileHandle(handle);
    setHasUnsavedChanges(false);
    setProjectVersion(extracted.version ?? DEFAULT_VERSION);
    setPendingNewProject(false);
    setPendingDelete(false);
    flash(`Loaded ${file.name}`, "success");
    return true;
  };

  const handleNativeFileSelection = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) {
      return;
    }
    await importProjectFromFile(file, null);
  };

  const handleLoadProject = async () => {
    setPendingNewProject(false);
    setPendingDelete(false);
    const picker = (window as any)?.showOpenFilePicker;
    if (typeof picker === "function") {
      try {
        const [handle] = await picker({
          multiple: false,
          types: [
            {
              description: "POSTI or Python files",
              accept: {
                "text/x-python": [".py"],
                "text/plain": [".txt"],
                "application/json": [".json"]
              }
            }
          ]
        });
        if (handle?.getFile) {
          const file = await handle.getFile();
          await importProjectFromFile(file, handle);
          return;
        }
      } catch (error: any) {
        if (error?.name === "AbortError") {
          return;
        }
        console.error(error);
        flash("Unable to access file picker. Falling back to manual upload.", "error");
      }
    }
    fileInputRef.current?.click();
  };

  const handleAddProfile = () => {
    setProfileModalState({ mode: "add" });
  };

  const handleEditProfile = () => {
    if (!currentProfile) {
      return;
    }
    setProfileModalState({ mode: "edit" });
  };

  const handleProfileModalSave = (result: ProfileModalResult) => {
    if (profileModalState?.mode === "add") {
      const taken = new Set(Object.keys(profiles));
      const key = slugify(result.label, taken);
      const profile: Profile = {
        label: result.label,
        description: result.description,
        preflight: result.preflight,
        steps: []
      };
      setProfiles((prev) => ({ ...prev, [key]: profile }));
      setProfileOrder((prev) => [...prev, key]);
      setActiveProfileKey(key);
      markDirty();
      flash(`Profile "${result.label}" added.`, "success");
    } else if (profileModalState?.mode === "edit" && currentProfile) {
      setProfiles((prev) => {
        const profile = prev[activeProfileKey];
        if (!profile) {
          return prev;
        }
        return {
          ...prev,
          [activeProfileKey]: {
            ...profile,
            label: result.label,
            description: result.description,
            preflight: result.preflight
          }
        };
      });
      markDirty();
      flash(`Profile "${result.label}" updated.`, "success");
    }
    setProfileModalState(null);
  };

  const handleRemoveProfile = () => {
    if (!currentProfile) {
      return;
    }
    setProfiles((prev) => {
      const next = { ...prev };
      delete next[activeProfileKey];
      return next;
    });
    setProfileOrder((prev) => prev.filter((key) => key !== activeProfileKey));
    const nextKey = profileOrder.find((key) => key !== activeProfileKey);
    setActiveProfileKey(nextKey ?? "");
    setActiveStepId(null);
    setStepForm(emptyStepForm);
    setSelectedStepIds(new Set());
    setPendingDelete(false);
    markDirty();
    flash("Profile removed.", "warning");
  };

  const handleRemoveClick = () => {
    if (!currentProfile) {
      return;
    }
    setPendingNewProject(false);
    if (!pendingDelete) {
      setPendingDelete(true);
      flash(`Press confirm to remove "${currentProfile.label}".`, "warning");
      return;
    }
    handleRemoveProfile();
  };

  const handleStepClick = (event: ReactMouseEvent<HTMLButtonElement>, stepId: string, index: number) => {
    if (!currentProfile) {
      return;
    }
    const isToggle = event.metaKey || event.ctrlKey;
    const isRange = event.shiftKey && selectionAnchor !== null;
    let nextSelection = new Set(selectedStepIds);

    if (isRange && selectionAnchor !== null) {
      const start = Math.min(selectionAnchor, index);
      const end = Math.max(selectionAnchor, index);
      nextSelection = new Set(
        currentProfile.steps.slice(start, end + 1).map((step) => step.id)
      );
      setSelectionAnchor(index);
    } else if (isToggle) {
      if (nextSelection.has(stepId)) {
        nextSelection.delete(stepId);
      } else {
        nextSelection.add(stepId);
      }
      setSelectionAnchor(index);
    } else {
      nextSelection = new Set([stepId]);
      setSelectionAnchor(index);
    }

    if (nextSelection.size === 0) {
      setActiveStepId(null);
      setSelectionAnchor(null);
    } else if (!nextSelection.has(activeStepId || "")) {
      const [first] = nextSelection;
      setActiveStepId(first ?? null);
    } else if (!isToggle) {
      setActiveStepId(stepId);
    }

    setSelectedStepIds(nextSelection);
  };

  const handleClearSelection = () => {
    setSelectedStepIds(new Set());
    setActiveStepId(null);
    setSelectionAnchor(null);
  };

  const selectedCount = selectedStepIds.size;

  return (
    <div className={`forge-shell theme-${theme}`}>
      <div className="top-row">
        <div className="panel profile-toolbar">
          <div className="panel-title">Profiles</div>
          <div className="profile-toolbar-row">
            <div className="profile-selector">
              <span>Active profile</span>
              <div
                className={`custom-select ${isProfileMenuOpen ? "open" : ""}`}
                ref={profileSelectRef}
              >
                <button
                  type="button"
                  id="profile-select-trigger"
                  className="custom-select-trigger"
                  onClick={() => {
                    if (!profileOrder.length) {
                      return;
                    }
                    setIsProfileMenuOpen((prev) => !prev);
                  }}
                  disabled={!profileOrder.length}
                  aria-haspopup="listbox"
                  aria-expanded={isProfileMenuOpen}
                >
                  <span>{currentProfile ? currentProfile.label : "No profiles"}</span>
                  <span className="chevron">{isProfileMenuOpen ? "▲" : "▼"}</span>
                </button>
                {isProfileMenuOpen && (
                  <ul className="custom-select-menu" role="listbox" aria-labelledby="profile-select-trigger">
                    {profileOrder.map((key) => (
                      <li key={key}>
                        <button
                          type="button"
                          className={key === activeProfileKey ? "active" : ""}
                          onClick={() => {
                            setActiveProfileKey(key);
                            setIsProfileMenuOpen(false);
                          }}
                        >
                          {profiles[key]?.label ?? key}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
            <div className="button-row compact profile-actions">
              <button className={`btn ghost ${hasProfiles ? "" : "pulse"}`} onClick={handleAddProfile}>
                Add
              </button>
              <button className="btn ghost" onClick={handleEditProfile} disabled={!currentProfile}>
                Edit
              </button>
              <button
                className={`btn ghost ${pendingDelete ? "danger" : ""}`}
                onClick={handleRemoveClick}
                disabled={!currentProfile}
              >
                {pendingDelete ? "Confirm" : "Remove"}
              </button>
            </div>
          </div>
          {!currentProfile && <p className="muted">No profiles yet. Start by adding one.</p>}
        </div>

        <div className="panel banner-panel">
          <div className="banner-controls">
            <button
              type="button"
              className="icon-button"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
              aria-label="Toggle light/dark mode"
            >
              {theme === "dark" ? "☀️" : "🌙"}
            </button>
            <button
              type="button"
              className="icon-button"
              aria-label="Open help menu"
              onClick={() => flash("Help & tips coming soon.", "info")}
            >
              ?
            </button>
          </div>
          <img src="/posti_banner_256.png" alt="Posti banner" />
        </div>

        <div className="panel operations-panel">
          <div className="panel-title">Operations</div>
          <div className="operations-actions">
            <div className="operation-button-stack">
              <button
                className={`btn ghost ${pendingNewProject ? "danger" : ""}`}
                onClick={handleNewProjectClick}
              >
                {pendingNewProject ? "Confirm" : "New project"}
              </button>
            </div>
            <div className="operation-button-stack">
              <button
                className={`btn ${isSaveMode ? "warning" : "ghost"}`}
                onClick={() => {
                  void handleProjectAction();
                }}
              >
                {isSaveMode ? "Save project" : "Load project"}
              </button>
            </div>
            <div className="operation-button-stack">
              <button
                className="btn ghost"
                onClick={handleBuildBinary}
                disabled={isBuildingBinary}
                title="Python libraries are build in. Binary is independed – You can run not having Python installed."
              >
                {isBuildingBinary ? "Building…" : "Build Binary"}
              </button>
            </div>
          </div>
          <div className="operations-meta">
            <div className="operations-status">
              <div className={`status-pill ${status ? status.level : "empty"}`}>
                <span>{status ? status.message : "No notifications"}</span>
              </div>
            </div>
            <div className="file-indicator operations-file">
              <div className="file-meta-row">
                <span>Current file</span>
                <span className="version-chip">v{projectVersion}</span>
              </div>
              <strong>{hasUnsavedChanges ? `${currentFileName} *` : currentFileName}</strong>
            </div>
          </div>
        </div>
      </div>

      {installPromptEvent && !installBannerDismissed && (
        <div className="pwa-banner">
          <div>
            <h3>Add Posti Forge to your home screen</h3>
            <p>Full offline mode and fast launch.</p>
          </div>
          <div className="button-row compact">
            <button
              className="btn ghost"
              onClick={() => {
                setInstallPromptEvent(null);
                setInstallBannerDismissed(true);
              }}
            >
              Maybe later
            </button>
            <button
              className="btn primary"
              onClick={async () => {
                if (!installPromptEvent?.prompt) {
                  return;
                }
                await installPromptEvent.prompt();
                const choiceResult = await installPromptEvent.userChoice.catch(() => null);
                if (!choiceResult || choiceResult.outcome === "accepted") {
                  setInstallPromptEvent(null);
                }
              }}
            >
              Install
            </button>
          </div>
        </div>
      )}

      <div className="workspace-grid">
        <section className="panel form-panel">
          <div className="panel-title">Step composer</div>
          <div className="form-grid">
            <div className="form-row dual">
              <label>
                <span>Title</span>
                <input
                  value={stepForm.title}
                  onChange={(event) => setStepForm((prev) => ({ ...prev, title: event.target.value }))}
                  placeholder="Step title"
                  disabled={!currentProfile}
                  onFocus={handlePlaceholderFocus}
                  onBlur={handlePlaceholderBlur}
                />
              </label>
              <label>
                <span>Description</span>
                <input
                  value={stepForm.description}
                  onChange={(event) => setStepForm((prev) => ({ ...prev, description: event.target.value }))}
                  placeholder="Optional context"
                  disabled={!currentProfile}
                  onFocus={handlePlaceholderFocus}
                  onBlur={handlePlaceholderBlur}
                />
              </label>
            </div>
            <label>
              <span>Command (use && for chained sub-steps)</span>
              <textarea
                value={stepForm.command}
                onChange={(event) => setStepForm((prev) => ({ ...prev, command: event.target.value }))}
                placeholder="sudo apt update && sudo apt upgrade -y"
                rows={6}
                disabled={!currentProfile}
                onFocus={handlePlaceholderFocus}
                onBlur={handlePlaceholderBlur}
              />
            </label>
          </div>
          <div className="button-row wrap">
            <button className="btn primary" onClick={handleAddStep} disabled={!currentProfile}>
              Add step
            </button>
            <button className="btn ghost" onClick={handleUpdateStep} disabled={!currentProfile || !activeStepId}>
              Update
            </button>
            <button
              className={`btn ghost toggle ${stepForm.confirm ? "active" : ""}`}
              onClick={() => handleConfirmToggle()}
              disabled={!currentProfile || !stepForm.command.trim()}
              title="Toggling that button means: Require confirmation before executing step"
            >
              Confirm
            </button>
            <button className="btn ghost" onClick={handleRemoveStep} disabled={!currentProfile || !activeStepId}>
              Remove
            </button>
            <button className="btn ghost" onClick={handleCloneStep} disabled={!currentProfile || !activeStepId}>
              Clone
            </button>
            <button className="btn ghost" onClick={() => setStepForm(emptyStepForm)} disabled={!currentProfile}>
              Clear form
            </button>
          </div>
        </section>

        <section className="panel steps-panel">
          <div className="panel-title">Steps</div>
          {currentProfile ? (
            <>
              <ul className="step-list">
                {currentProfile.steps.length === 0 && <li className="muted">No steps yet – use the composer.</li>}
                {currentProfile.steps.map((step, index) => (
                  <li
                    key={step.id}
                    draggable={false}
                    className={[
                      "step-card",
                      !step.enabled ? "disabled" : "",
                      selectedStepIds.has(step.id) ? "selected" : "",
                      activeStepId === step.id ? "active" : ""
                    ]
                      .filter(Boolean)
                      .join(" ")}
                  >
                    <button type="button" className="step-main" onClick={(event) => handleStepClick(event, step.id, index)}>
                      <div className="step-index">{String(index + 1).padStart(2, "0")}</div>
                      <div className="step-body">
                        <div className="step-title-row">
                          <span className="step-title">{step.title}</span>
                          {step.confirm && <span className="pill confirm">confirm</span>}
                        </div>
                      </div>
                      <span className={`status-chip ${step.enabled ? "on" : "off"}`}>
                        {step.enabled ? "Enabled" : "Disabled"}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
              <div className="bulk-row">
                <span className="muted">
                  {selectedCount} selected · {stepCount} total
                </span>
                <div className="button-row compact">
                  <button
                    className="btn ghost icon-only"
                    disabled={!currentProfile || !activeStepId}
                    onClick={() => handleMoveStep(-1)}
                    title="Move up"
                  >
                    ↑
                  </button>
                  <button
                    className="btn ghost icon-only"
                    disabled={!currentProfile || !activeStepId}
                    onClick={() => handleMoveStep(1)}
                    title="Move down"
                  >
                    ↓
                  </button>
                  <button className="btn ghost" disabled={!selectedCount} onClick={() => handleBulkToggle(false)}>
                    Disable selected
                  </button>
                  <button className="btn ghost" disabled={!selectedCount} onClick={() => handleBulkToggle(true)}>
                    Enable selected
                  </button>
                  <button className="btn ghost" disabled={!selectedCount} onClick={handleClearSelection}>
                    Clear selection
                  </button>
                </div>
              </div>
            </>
          ) : (
            <p className="muted">Create or load a profile to start adding steps.</p>
          )}
        </section>
      </div>

      <section className="panel preview-panel">
        <div className="panel-title">posti.py preview</div>
        <div className={`preview ${preview ? "has-code" : "empty"}`}>
          {preview ? (
            <pre className="code-block syntax-highlight" dangerouslySetInnerHTML={{ __html: highlightedPreview }} />
          ) : (
            <p className="preview-placeholder muted">Click "Generate preview" to assemble the posti.py runtime.</p>
          )}
        </div>
        <div className="button-row wrap">
          <button className="btn primary" onClick={handleGenerateScript} disabled={projectEmpty}>
            Generate preview
          </button>
          <button className="btn ghost" onClick={handleCopyPreview} disabled={!preview}>
            Copy to clipboard
          </button>
        </div>
      </section>

      {profileModalState && (
        <ProfileModal
          mode={profileModalState.mode}
          initialValues={
            profileModalState.mode === "edit" && currentProfile
              ? {
                  label: currentProfile.label,
                  description: currentProfile.description,
                  preflight: currentProfile.preflight
                }
              : undefined
          }
          onCancel={() => setProfileModalState(null)}
          onSave={handleProfileModalSave}
        />
      )}

      <input
        ref={fileInputRef}
        type="file"
        accept=".py,.txt,.json"
        hidden
        onChange={handleNativeFileSelection}
        data-testid="file-input"
        aria-label="Select a posti.py project to load"
        title="Select a posti.py project to load"
      />
    </div>
  );
};

type ProfileModalProps = {
  mode: "add" | "edit";
  initialValues?: ProfileModalResult;
  onCancel: () => void;
  onSave: (result: ProfileModalResult) => void;
};

const ProfileModal = ({ mode, initialValues, onCancel, onSave }: ProfileModalProps) => {
  const [label, setLabel] = useState(initialValues?.label ?? "");
  const [description, setDescription] = useState(initialValues?.description ?? "");
  const [preflight, setPreflight] = useState((initialValues?.preflight ?? []).join("\n"));
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!label.trim()) {
      setError("Profile label cannot be empty.");
      return;
    }
    const checklist = preflight
      .split("\n")
      .map((item) => item.trim())
      .filter(Boolean);
    onSave({
      label: label.trim(),
      description: description.trim(),
      preflight: checklist
    });
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <form className="modal" onSubmit={handleSubmit}>
        <h3>{mode === "add" ? "Add profile" : "Edit profile"}</h3>
        <label>
          <span>Profile label</span>
          <input
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            autoFocus
            placeholder="Kiosk profile name"
            onFocus={handlePlaceholderFocus}
            onBlur={handlePlaceholderBlur}
          />
        </label>
        <label>
          <span>Description</span>
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            rows={3}
            placeholder="Explain the target, scope, or intent."
            onFocus={handlePlaceholderFocus}
            onBlur={handlePlaceholderBlur}
          />
        </label>
        <label>
          <span>Pre-flight checklist (one per line)</span>
          <textarea
            value={preflight}
            onChange={(event) => setPreflight(event.target.value)}
            rows={4}
            placeholder="Ensure ethernet is connected"
            onFocus={handlePlaceholderFocus}
            onBlur={handlePlaceholderBlur}
          />
        </label>
        {error && <p className="error">{error}</p>}
        <div className="button-row">
          <button type="button" className="btn ghost" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" className="btn primary">
            {mode === "add" ? "Save profile" : "Update profile"}
          </button>
        </div>
      </form>
    </div>
  );
};

export default App;
