"use client";

import { useState, useEffect, useCallback } from "react";
import { useDropzone } from "react-dropzone";
import toast from "react-hot-toast";
import {
  Upload, Link, Trash2, RefreshCw, FileText, Globe,
  CheckCircle, AlertCircle, Loader2, Database, Filter,
  ChevronDown, X, Plus
} from "lucide-react";
import {
  listDocuments, uploadDocument, addDocumentURL,
  deleteDocument, reindexDocument, getKBStats,
  Document, KBStats
} from "@/lib/api_12082026";
import { clsx } from "clsx";
import { formatDistanceToNow } from "date-fns";

const CATEGORIES = [
  { value: "all", label: "All categories" },
  { value: "runbook", label: "Runbook" },
  { value: "postmortem", label: "Postmortem" },
  { value: "architecture", label: "Architecture" },
  { value: "api_docs", label: "API Docs" },
  { value: "playbook", label: "Playbook" },
  { value: "configuration", label: "Configuration" },
  { value: "other", label: "Other" },
];

const UPLOAD_CATEGORIES = [
  { value: "auto", label: "Auto-detect" },
  { value: "runbook", label: "Runbook" },
  { value: "postmortem", label: "Postmortem" },
  { value: "architecture", label: "Architecture" },
  { value: "api_docs", label: "API Docs" },
  { value: "playbook", label: "Playbook" },
  { value: "configuration", label: "Configuration" },
  { value: "other", label: "Other" },
];

function inferCategoryFromFilename(filename: string): string {
  const name = filename.toLowerCase();
  if (name.startsWith("rb-") || name.startsWith("rb_") || name.includes("runbook")) {
    return "runbook";
  }
  if (name.startsWith("pm-") || name.startsWith("pm_") || name.includes("postmortem")) {
    return "postmortem";
  }
  if (name.includes("arch") || name.includes("architecture")) {
    return "architecture";
  }
  if (name.includes("api") || name.includes("swagger") || name.includes("openapi")) {
    return "api_docs";
  }
  if (name.includes("playbook")) {
    return "playbook";
  }
  if (name.includes("config")) {
    return "configuration";
  }
  return "other";
}

export default function AdminPage() {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [stats, setStats] = useState<KBStats | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [uploadCategory, setUploadCategory] = useState("auto");
  const [showURLForm, setShowURLForm] = useState(false);
  const [urlForm, setUrlForm] = useState({ url: "", title: "", category: "other", service_tag: "" });
  const [uploadingIds, setUploadingIds] = useState<Set<string>>(new Set());

  const loadData = useCallback(async () => {
    try {
      const [docs, kbStats] = await Promise.all([
        listDocuments(categoryFilter !== "all" ? { category: categoryFilter } : undefined),
        getKBStats(),
      ]);
      setDocuments(docs);
      setStats(kbStats);
    } catch {
      toast.error("Failed to load documents");
    } finally {
      setIsLoading(false);
    }
  }, [categoryFilter]);

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 3000); // Poll for status updates
    return () => clearInterval(interval);
  }, [loadData]);

  const onDrop = useCallback(async (acceptedFiles: File[]) => {
    for (const file of acceptedFiles) {
      const tempId = `uploading-${Date.now()}`;
      setUploadingIds((prev) => new Set(prev).add(tempId));

      try {
        const targetCategory = uploadCategory === "auto"
          ? inferCategoryFromFilename(file.name)
          : uploadCategory;

        await uploadDocument(file, targetCategory);
        toast.success(`${file.name} queued for indexing (${targetCategory})`);
        await loadData();
      } catch (err: any) {
        toast.error(err.message || `Failed to upload ${file.name}`);
      } finally {
        setUploadingIds((prev) => { const s = new Set(prev); s.delete(tempId); return s; });
      }
    }
  }, [loadData, uploadCategory]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      "application/pdf": [".pdf"],
      "text/plain": [".txt"],
      "text/markdown": [".md", ".markdown"],
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
    },
    maxSize: 50 * 1024 * 1024,
  });

  const handleAddURL = async () => {
    if (!urlForm.url || !urlForm.title) {
      toast.error("URL and title are required");
      return;
    }
    try {
      await addDocumentURL(urlForm.url, urlForm.title, urlForm.category, urlForm.service_tag || undefined);
      toast.success("URL queued for indexing");
      setShowURLForm(false);
      setUrlForm({ url: "", title: "", category: "other", service_tag: "" });
      await loadData();
    } catch (err: any) {
      toast.error(err.message || "Failed to add URL");
    }
  };

  const handleDelete = async (doc: Document) => {
    if (!confirm(`Delete "${doc.filename}"? This cannot be undone.`)) return;
    try {
      await deleteDocument(doc.id);
      toast.success("Document deleted");
      await loadData();
    } catch {
      toast.error("Failed to delete document");
    }
  };

  const handleReindex = async (doc: Document) => {
    try {
      await reindexDocument(doc.id);
      toast.success("Reindexing started");
      await loadData();
    } catch {
      toast.error("Failed to reindex");
    }
  };

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-white">Knowledge Base</h1>
        <p className="text-gray-400 text-sm mt-1">
          Manage documents indexed for RAG search
        </p>
      </div>

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-4 gap-4">
          {[
            { label: "Total Documents", value: stats.total_documents, icon: FileText, color: "blue" },
            { label: "Total Chunks", value: stats.total_chunks.toLocaleString(), icon: Database, color: "purple" },
            { label: "Indexed", value: stats.indexed_documents, icon: CheckCircle, color: "green" },
            { label: "Processing", value: stats.processing_documents, icon: Loader2, color: "yellow" },
          ].map((stat) => (
            <div key={stat.label} className="bg-gray-900 border border-gray-800 rounded-xl p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-gray-400 text-xs">{stat.label}</span>
                <stat.icon className={clsx("w-4 h-4", {
                  "text-blue-400": stat.color === "blue",
                  "text-purple-400": stat.color === "purple",
                  "text-green-400": stat.color === "green",
                  "text-yellow-400": stat.color === "yellow",
                })} />
              </div>
              <div className="text-2xl font-bold text-white">{stat.value}</div>
            </div>
          ))}
        </div>
      )}

      {/* Upload Area */}
      <div className="grid grid-cols-2 gap-4">
        {/* File Drop */}
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between px-1">
            <span className="text-xs text-gray-400 font-medium">Upload Category:</span>
            <select
              value={uploadCategory}
              onChange={(e) => setUploadCategory(e.target.value)}
              className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1 text-xs text-white outline-none focus:border-blue-500"
            >
              {UPLOAD_CATEGORIES.map((c) => (
                <option key={c.value} value={c.value}>{c.label}</option>
              ))}
            </select>
          </div>
          <div
            {...getRootProps()}
            className={clsx(
              "border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-all flex-1 flex flex-col items-center justify-center",
              isDragActive
                ? "border-blue-500 bg-blue-500/10"
                : "border-gray-700 hover:border-gray-500 hover:bg-gray-900/50"
            )}
          >
            <input {...getInputProps()} />
            <Upload className="w-8 h-8 text-gray-500 mx-auto mb-2" />
            <p className="text-gray-300 font-medium text-sm">
              {isDragActive ? "Drop files here" : "Drag & drop files"}
            </p>
            <p className="text-gray-500 text-xs mt-1">PDF, TXT, MD, DOCX — max 50MB</p>
            <button className="mt-3 px-4 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs rounded-lg transition-colors">
              Browse files
            </button>
          </div>
        </div>

        {/* URL Form */}
        <div className="border border-gray-800 rounded-xl p-6 bg-gray-900/50">
          <div className="flex items-center gap-2 mb-4">
            <Globe className="w-5 h-5 text-blue-400" />
            <h3 className="text-white font-medium text-sm">Add Wiki URL</h3>
          </div>

          {showURLForm ? (
            <div className="space-y-3">
              <input
                type="url"
                placeholder="https://wiki.company.com/runbook/..."
                value={urlForm.url}
                onChange={(e) => setUrlForm({ ...urlForm, url: e.target.value })}
                className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 outline-none focus:border-blue-500"
              />
              <input
                type="text"
                placeholder="Title (e.g. Payment Service Runbook)"
                value={urlForm.title}
                onChange={(e) => setUrlForm({ ...urlForm, title: e.target.value })}
                className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 outline-none focus:border-blue-500"
              />
              <div className="grid grid-cols-2 gap-2">
                <select
                  value={urlForm.category}
                  onChange={(e) => setUrlForm({ ...urlForm, category: e.target.value })}
                  className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white outline-none"
                >
                  {CATEGORIES.slice(1).map((c) => (
                    <option key={c.value} value={c.value}>{c.label}</option>
                  ))}
                </select>
                <input
                  type="text"
                  placeholder="Service tag (optional)"
                  value={urlForm.service_tag}
                  onChange={(e) => setUrlForm({ ...urlForm, service_tag: e.target.value })}
                  className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 outline-none focus:border-blue-500"
                />
              </div>
              <div className="flex gap-2">
                <button onClick={handleAddURL} className="flex-1 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm rounded-lg transition-colors">
                  Index URL
                </button>
                <button onClick={() => setShowURLForm(false)} className="px-3 py-2 bg-gray-800 text-gray-400 text-sm rounded-lg hover:text-white transition-colors">
                  <X className="w-4 h-4" />
                </button>
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-24 gap-3">
              <p className="text-gray-500 text-xs text-center">
                Index wiki pages directly from their URL — no need to export PDFs
              </p>
              <button
                onClick={() => setShowURLForm(true)}
                className="flex items-center gap-2 px-4 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm rounded-lg transition-colors"
              >
                <Plus className="w-4 h-4" /> Add URL
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Filter */}
      <div className="flex items-center gap-3">
        <Filter className="w-4 h-4 text-gray-500" />
        <div className="flex gap-2">
          {CATEGORIES.map((cat) => (
            <button
              key={cat.value}
              onClick={() => setCategoryFilter(cat.value)}
              className={clsx(
                "px-3 py-1 rounded-full text-xs transition-colors",
                categoryFilter === cat.value
                  ? "bg-blue-600 text-white"
                  : "bg-gray-800 text-gray-400 hover:text-gray-200"
              )}
            >
              {cat.label}
            </button>
          ))}
        </div>
      </div>

      {/* Document List */}
      <div className="space-y-2">
        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin text-blue-400" />
          </div>
        ) : documents.length === 0 ? (
          <div className="text-center py-12 text-gray-500">
            <Database className="w-12 h-12 mx-auto mb-3 opacity-30" />
            <p>No documents indexed yet</p>
            <p className="text-xs mt-1">Upload files or add wiki URLs to get started</p>
          </div>
        ) : (
          documents.map((doc) => (
            <DocumentRow
              key={doc.id}
              doc={doc}
              onDelete={handleDelete}
              onReindex={handleReindex}
            />
          ))
        )}
      </div>
    </div>
  );
}

function DocumentRow({
  doc, onDelete, onReindex
}: {
  doc: Document;
  onDelete: (doc: Document) => void;
  onReindex: (doc: Document) => void;
}) {
  const statusConfig = {
    indexed: { icon: CheckCircle, color: "text-green-400", label: "Indexed" },
    processing: { icon: Loader2, color: "text-yellow-400", label: "Processing" },
    error: { icon: AlertCircle, color: "text-red-400", label: "Error" },
  }[doc.status];

  return (
    <div className="flex items-center gap-4 bg-gray-900 border border-gray-800 rounded-xl px-4 py-3 hover:border-gray-700 transition-colors">
      <div className="flex-shrink-0">
        {doc.source === "url" ? (
          <Globe className="w-5 h-5 text-blue-400" />
        ) : (
          <FileText className="w-5 h-5 text-gray-400" />
        )}
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-white text-sm font-medium truncate">{doc.title || doc.filename}</span>
          <span className="text-xs px-2 py-0.5 bg-gray-800 text-gray-400 rounded-full flex-shrink-0">
            {doc.category}
          </span>
          {doc.service_tag && (
            <span className="text-xs px-2 py-0.5 bg-blue-900/30 text-blue-400 rounded-full flex-shrink-0">
              {doc.service_tag}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 mt-1">
          <div className={clsx("flex items-center gap-1 text-xs", statusConfig.color)}>
            <statusConfig.icon className={clsx("w-3 h-3", doc.status === "processing" && "animate-spin")} />
            {statusConfig.label}
            {doc.status === "indexed" && doc.chunks_count > 0 && (
              <span className="text-gray-500 ml-1">· {doc.chunks_count} chunks</span>
            )}
          </div>
          {doc.indexed_at && (
            <span className="text-xs text-gray-600">
              {formatDistanceToNow(new Date(doc.indexed_at), { addSuffix: true })}
            </span>
          )}
          {doc.error_message && (
            <span className="text-xs text-red-400 truncate max-w-xs">{doc.error_message}</span>
          )}
        </div>
      </div>

      <div className="flex items-center gap-1 flex-shrink-0">
        <button
          onClick={() => onReindex(doc)}
          disabled={doc.status === "processing"}
          className="p-2 text-gray-500 hover:text-gray-300 disabled:opacity-50 transition-colors"
          title="Reindex"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
        <button
          onClick={() => onDelete(doc)}
          className="p-2 text-gray-500 hover:text-red-400 transition-colors"
          title="Delete"
        >
          <Trash2 className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}