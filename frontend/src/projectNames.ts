const stripExtension = (name: string) => name.replace(/\.[^.]+$/, "");

export const extractProjectName = (name: string | null | undefined): string => {
  if (!name) {
    return "";
  }
  const stripped = stripExtension(name.trim());
  const currentMatch = stripped.match(/^(.*)_posti_\d+(?:\.\d+){0,2}$/i);
  const legacyMatch = stripped.match(/^(.*)_v\d+(?:\.\d+){0,2}$/i);
  return (currentMatch?.[1] || legacyMatch?.[1] || stripped).trim();
};

export const normalizeProjectName = (name: string): string =>
  name.trim().replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^[-_]+|[-_]+$/g, "");
