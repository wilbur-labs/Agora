"use client";

import { useCallback, useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { ArtifactInfo, fetchArtifacts, fetchArtifactContent, getArtifactDownloadUrl } from "@/lib/api";

const EXT_ICONS: Record<string, string> = {
  py: "🐍", js: "📜", ts: "📜", tsx: "📜", jsx: "📜",
  html: "🌐", css: "🎨", json: "📋", yaml: "⚙️", yml: "⚙️",
  md: "📝", txt: "📄", sh: "🔧", toml: "⚙️", sql: "🗃️",
};

interface TreeNode {
  name: string;
  path?: string;        // full path (only for files)
  info?: ArtifactInfo;  // metadata (only for files)
  children: TreeNode[];
}

function buildTree(items: ArtifactInfo[]): TreeNode {
  const root: TreeNode = { name: "", children: [] };
  // Find common prefix to shorten display
  if (items.length === 0) return root;
  const paths = items.map((i) => i.path.split("/"));
  let prefixLen = 0;
  if (paths.length > 1) {
    const first = paths[0];
    outer: for (let i = 0; i < first.length - 1; i++) {
      for (const p of paths) {
        if (p[i] !== first[i]) break outer;
      }
      prefixLen = i + 1;
    }
  } else if (paths[0].length > 1) {
    prefixLen = paths[0].length - 1;
  }

  for (const item of items) {
    const parts = item.path.split("/").slice(prefixLen);
    let node = root;
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      const isFile = i === parts.length - 1;
      let child = node.children.find((c) => c.name === part && (isFile ? !!c.path : !c.path));
      if (!child) {
        child = isFile
          ? { name: part, path: item.path, info: item, children: [] }
          : { name: part, children: [] };
        node.children.push(child);
      }
      node = child;
    }
  }
  // Sort: folders first, then files, alphabetically
  const sortNodes = (nodes: TreeNode[]) => {
    nodes.sort((a, b) => {
      const aDir = !a.path ? 0 : 1;
      const bDir = !b.path ? 0 : 1;
      if (aDir !== bDir) return aDir - bDir;
      return a.name.localeCompare(b.name);
    });
    nodes.forEach((n) => sortNodes(n.children));
  };
  sortNodes(root.children);
  return root;
}

function FileTreeNode({ node, depth, selected, onSelect, onDownload }: {
  node: TreeNode; depth: number; selected: string | null;
  onSelect: (path: string) => void; onDownload: (path: string) => void;
}) {
  const [open, setOpen] = useState(true);
  const isDir = !node.path;
  const ext = node.info?.ext ?? "";

  if (isDir) {
    return (
      <div>
        <div
          onClick={() => setOpen(!open)}
          className="flex items-center gap-1.5 px-3 py-1 text-xs cursor-pointer hover:bg-accent/50 transition-colors"
          style={{ paddingLeft: `${depth * 16 + 12}px` }}
        >
          <span className="text-muted-foreground">{open ? "▾" : "▸"}</span>
          <span>📁</span>
          <span className="font-medium">{node.name}</span>
        </div>
        {open && node.children.map((child, i) => (
          <FileTreeNode key={`${child.name}-${i}`} node={child} depth={depth + 1} selected={selected} onSelect={onSelect} onDownload={onDownload} />
        ))}
      </div>
    );
  }

  return (
    <div
      onClick={() => onSelect(node.path!)}
      className={cn(
        "flex items-center gap-1.5 px-3 py-1 text-xs cursor-pointer transition-colors group",
        selected === node.path ? "bg-accent" : "hover:bg-accent/50",
      )}
      style={{ paddingLeft: `${depth * 16 + 12}px` }}
    >
      <span>{EXT_ICONS[ext] ?? "📄"}</span>
      <span className="flex-1 truncate font-mono">{node.name}</span>
      <span className="text-muted-foreground text-[10px]">
        {node.info && node.info.size > 1024 ? `${(node.info.size / 1024).toFixed(1)}K` : `${node.info?.size ?? 0}B`}
      </span>
      <button
        onClick={(e) => { e.stopPropagation(); onDownload(node.path!); }}
        className="text-muted-foreground hover:text-foreground opacity-0 group-hover:opacity-100"
      >⬇</button>
    </div>
  );
}

interface ArtifactsPanelProps {
  artifacts: string[];
  open: boolean;
  onClose: () => void;
}

export function ArtifactsPanel({ artifacts: artifactPaths, open, onClose }: ArtifactsPanelProps) {
  const [items, setItems] = useState<ArtifactInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<string>("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (artifactPaths.length === 0) { setItems([]); return; }
    fetchArtifacts().then(setItems).catch(() => {});
  }, [artifactPaths]);

  useEffect(() => {
    if (artifactPaths.length > 0 && !selected) {
      setSelected(artifactPaths[artifactPaths.length - 1]);
    }
  }, [artifactPaths, selected]);

  useEffect(() => {
    if (!selected) { setContent(""); return; }
    setLoading(true);
    fetchArtifactContent(selected).then(setContent).catch(() => setContent("(failed to load)")).finally(() => setLoading(false));
  }, [selected]);

  const handleDownload = useCallback((path: string) => {
    window.open(getArtifactDownloadUrl(path), "_blank");
  }, []);

  if (!open) return null;

  const tree = buildTree(items);

  return (
    <div className="w-96 min-w-96 border-l border-border bg-sidebar flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <h2 className="text-sm font-semibold">📁 Artifacts ({items.length} files)</h2>
        <button onClick={onClose} className="text-muted-foreground hover:text-foreground text-xs">✕</button>
      </div>

      <div className="flex-shrink-0 max-h-64 overflow-y-auto border-b border-border py-1">
        {items.length === 0 ? (
          <p className="text-xs text-muted-foreground p-4">No files yet.</p>
        ) : tree.children.map((node, i) => (
          <FileTreeNode key={`${node.name}-${i}`} node={node} depth={0} selected={selected} onSelect={setSelected} onDownload={handleDownload} />
        ))}
      </div>

      <div className="flex-1 overflow-hidden flex flex-col">
        {selected ? (
          <>
            <div className="flex items-center gap-2 px-4 py-2 border-b border-border text-xs text-muted-foreground">
              <span className="font-mono truncate flex-1">{selected.split("/").pop()}</span>
              <button onClick={() => handleDownload(selected)} className="hover:text-foreground">📥</button>
              <button onClick={() => { navigator.clipboard.writeText(content); }} className="hover:text-foreground">📋</button>
            </div>
            <div className="flex-1 overflow-auto">
              {loading ? (
                <p className="text-xs text-muted-foreground p-4">Loading…</p>
              ) : (
                <pre className="p-4 text-xs font-mono leading-relaxed whitespace-pre-wrap break-all">{content}</pre>
              )}
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-xs text-muted-foreground">
            Select a file to preview
          </div>
        )}
      </div>
    </div>
  );
}
