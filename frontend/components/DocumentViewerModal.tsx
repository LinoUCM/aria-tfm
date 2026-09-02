"use client";

import { X } from "lucide-react";
import ReactMarkdown from "react-markdown";

interface DocumentViewerModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  content: string;
}

export default function DocumentViewerModal({
  isOpen,
  onClose,
  title,
  content,
}: DocumentViewerModalProps) {
  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-3xl bg-gray-900 border border-gray-800 rounded-xl p-6 shadow-2xl space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-gray-800 pb-3">
          <div className="flex items-center gap-2 text-white font-semibold text-base truncate">
            <span className="truncate">{title}</span>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white p-1 flex-shrink-0"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="max-h-[70vh] overflow-y-auto pr-1 text-sm text-gray-300">
          <ReactMarkdown
            components={{
              h1: ({ children }) => (
                <h1 className="text-lg font-bold text-white mt-4 mb-2 first:mt-0">{children}</h1>
              ),
              h2: ({ children }) => (
                <h2 className="text-base font-bold text-white mt-4 mb-2">{children}</h2>
              ),
              h3: ({ children }) => (
                <h3 className="text-sm font-bold text-gray-200 mt-3 mb-1.5">{children}</h3>
              ),
              p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
              ul: ({ children }) => (
                <ul className="list-disc pl-4 space-y-1 my-2">{children}</ul>
              ),
              ol: ({ children }) => (
                <ol className="list-decimal pl-4 space-y-1 my-2">{children}</ol>
              ),
              li: ({ children }) => <li className="text-gray-300">{children}</li>,
              a: ({ href, children }) => (
                <a
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-blue-400 hover:text-blue-300 underline"
                >
                  {children}
                </a>
              ),
              code: ({ children }) => (
                <code className="bg-gray-950 px-1.5 py-0.5 rounded text-xs font-mono text-emerald-400 border border-gray-800">
                  {children}
                </code>
              ),
              pre: ({ children }) => (
                <pre className="bg-gray-950 border border-gray-800 rounded-lg p-3 my-2 overflow-x-auto text-xs">
                  {children}
                </pre>
              ),
              table: ({ children }) => (
                <div className="overflow-x-auto my-2">
                  <table className="w-full text-left text-xs border border-gray-800">
                    {children}
                  </table>
                </div>
              ),
              th: ({ children }) => (
                <th className="border border-gray-800 bg-gray-800/60 px-2 py-1 font-semibold text-gray-200">
                  {children}
                </th>
              ),
              td: ({ children }) => (
                <td className="border border-gray-800 px-2 py-1">{children}</td>
              ),
            }}
          >
            {content}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  );
}
