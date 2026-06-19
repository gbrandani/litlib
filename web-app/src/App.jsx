import React, { useState, useEffect, useRef } from 'react';
import './App.css';

const API_BASE = window.location.origin.includes('localhost:517') || window.location.origin.includes('127.0.0.1:517')
  ? 'https://127.0.0.1:8000'
  : '';

export default function App() {
  // Word Add-in State variables
  const [isWordAddin, setIsWordAddin] = useState(false);
  const [isOfficeReady, setIsOfficeReady] = useState(false);
  const [wordStyle, setWordStyle] = useState('nature');
  const [wordSearchQuery, setWordSearchQuery] = useState('');
  const [wordPapers, setWordPapers] = useState([]);
  const [selectedWordPaperIds, setSelectedWordPaperIds] = useState([]);
  const [papers, setPapers] = useState([]);
  const selectedPapersDetails = React.useMemo(() => {
    return selectedWordPaperIds.map(id => {
      return wordPapers.find(p => p.paper_id === id) || papers.find(p => p.paper_id === id);
    }).filter(Boolean);
  }, [selectedWordPaperIds, wordPapers, papers]);

  const displayedWordPapers = React.useMemo(() => {
    const selected = selectedPapersDetails;
    const nonSelectedSearch = wordPapers.filter(
      p => !selectedWordPaperIds.includes(p.paper_id)
    );
    return [...selected, ...nonSelectedSearch];
  }, [selectedPapersDetails, selectedWordPaperIds, wordPapers]);
  const [wordStatus, setWordStatus] = useState(null); // { type: 'success'|'error'|'info', message: '' }
  const [isWordActionLoading, setIsWordActionLoading] = useState(false);

  useEffect(() => {
    // Check if we are running on the word.html page or Office context is available
    const isWordPath = window.location.pathname.includes('word.html');
    if (isWordPath || window.Office) {
      setIsWordAddin(true);
      
      // Initialize Office
      if (window.Office) {
        window.Office.onReady((info) => {
          if (info.host === window.Office.HostType.Word) {
            setIsOfficeReady(true);
            
            // Try to load saved citation style from document settings
            try {
              const savedStyle = window.Office.context.document.settings.get('citationStyle');
              if (savedStyle) {
                setWordStyle(savedStyle);
              }
            } catch (e) {
              console.error("Failed to load saved citation style:", e);
            }
          } else {
            // Not Word (e.g. debugging in browser)
            setIsOfficeReady(true);
          }
        });
      } else {
        // Mock Office readiness if window.Office isn't loaded yet (e.g. testing)
        setIsOfficeReady(true);
      }
    }
  }, []);

  const handleWordSearch = async (query) => {
    try {
      const params = new URLSearchParams();
      if (query) {
        params.append('q', query);
      }
      params.append('scope', 'auto');
      const res = await fetch(`${API_BASE}/api/papers/search?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        setWordPapers(data);
      }
    } catch (err) {
      console.error('Word search failed:', err);
    }
  };

  useEffect(() => {
    if (isWordAddin) {
      handleWordSearch(wordSearchQuery);
    }
  }, [isWordAddin, wordSearchQuery]);

  const handleInsertCitation = async () => {
    if (selectedWordPaperIds.length === 0) return;
    setIsWordActionLoading(true);
    setWordStatus({ type: 'info', message: 'Inserting citation...' });
    
    try {
      if (!window.Word) {
        throw new Error("Microsoft Word API is not available in this environment.");
      }
      
      await window.Word.run(async (context) => {
        const selection = context.document.getSelection();
        const cc = selection.insertContentControl();
        cc.tag = 'lib-cite:' + selectedWordPaperIds.join(',');
        cc.title = 'Citation';
        cc.appearance = window.Word.ContentControlAppearance.boundingBox;
        cc.insertText('[Ref]', window.Word.InsertLocation.replace);
        
        // Move selection after the content control so cursor is not stuck inside
        const rangeAfter = cc.getRange(window.Word.RangeLocation.after);
        rangeAfter.select();
        
        await context.sync();
      });
      
      setWordStatus({ type: 'success', message: 'Citation inserted! Formatting references...' });
      setSelectedWordPaperIds([]); // clear selection
      
      // Wait 200ms to allow Word layout engine to finish updating
      await new Promise(resolve => setTimeout(resolve, 200));
      
      // Auto refresh so the newly inserted citation gets formatted immediately
      await handleRefreshCitations(wordStyle);
      
    } catch (err) {
      console.error("Failed to insert citation:", err);
      setWordStatus({ type: 'error', message: `Insert failed: ${err.message || err}` });
    } finally {
      setIsWordActionLoading(false);
    }
  };

  const handleInsertBibliography = async () => {
    setIsWordActionLoading(true);
    setWordStatus({ type: 'info', message: 'Inserting bibliography...' });
    
    try {
      if (!window.Word) {
        throw new Error("Microsoft Word API is not available in this environment.");
      }
      
      await window.Word.run(async (context) => {
        const selection = context.document.getSelection();
        const cc = selection.insertContentControl();
        cc.tag = 'lib-bibliography';
        cc.title = 'Bibliography';
        cc.appearance = window.Word.ContentControlAppearance.boundingBox;
        cc.insertText('Bibliography will appear here.', window.Word.InsertLocation.replace);
        
        // Move selection after the bibliography control
        const rangeAfter = cc.getRange(window.Word.RangeLocation.after);
        rangeAfter.select();
        
        await context.sync();
      });
      
      setWordStatus({ type: 'success', message: 'Bibliography inserted! Formatting references...' });
      
      // Wait 200ms to allow Word layout engine to finish updating
      await new Promise(resolve => setTimeout(resolve, 200));
      
      // Auto refresh to fill the bibliography
      await handleRefreshCitations(wordStyle);
      
    } catch (err) {
      console.error("Failed to insert bibliography:", err);
      setWordStatus({ type: 'error', message: `Insert failed: ${err.message || err}` });
    } finally {
      setIsWordActionLoading(false);
    }
  };

  const handleRefreshCitations = async (styleName) => {
    setIsWordActionLoading(true);
    setWordStatus({ type: 'info', message: 'Refreshing references...' });
    
    try {
      if (!window.Word) {
        throw new Error("Microsoft Word API is not available in this environment.");
      }
      
      let citationControls = [];
      let payloadCitations = [];
      
      await window.Word.run(async (context) => {
        const contentControls = context.document.contentControls;
        contentControls.load('tag, id, title, cannotEdit');
        await context.sync();
        
        for (let i = 0; i < contentControls.items.length; i++) {
          const cc = contentControls.items[i];
          const tag = cc.tag;
          if (tag && tag.startsWith('lib-cite:')) {
            const paperIdsStr = tag.substring('lib-cite:'.length);
            const paperIds = paperIdsStr.split(',').filter(Boolean);
            citationControls.push({
              id: cc.id,
              tag: tag,
              paperIds: paperIds
            });
            payloadCitations.push({
              id: cc.id.toString(),
              paper_ids: paperIds
            });
          }
        }
      });
      
      if (payloadCitations.length === 0) {
        // If there are no citations but we have a bibliography control, we should still clear it
        // Phase 1: Unlock bibliography
        await window.Word.run(async (context) => {
          const contentControls = context.document.contentControls;
          contentControls.load('tag, id, cannotEdit');
          await context.sync();
          for (let i = 0; i < contentControls.items.length; i++) {
            const cc = contentControls.items[i];
            if (cc.tag === 'lib-bibliography') {
              cc.cannotEdit = false;
            }
          }
          await context.sync();
        });
        
        // Phase 2: Clear and insert placeholder
        await window.Word.run(async (context) => {
          const contentControls = context.document.contentControls;
          contentControls.load('tag, id');
          await context.sync();
          for (let i = 0; i < contentControls.items.length; i++) {
            const cc = contentControls.items[i];
            if (cc.tag === 'lib-bibliography') {
              cc.clear();
              cc.insertText('Bibliography will appear here.', window.Word.InsertLocation.replace);
            }
          }
          await context.sync();
        });
        
        // Phase 3: Lock bibliography again
        await window.Word.run(async (context) => {
          const contentControls = context.document.contentControls;
          contentControls.load('tag, id');
          await context.sync();
          for (let i = 0; i < contentControls.items.length; i++) {
            const cc = contentControls.items[i];
            if (cc.tag === 'lib-bibliography') {
              cc.cannotEdit = true;
            }
          }
          await context.sync();
        });
        
        setWordStatus({ type: 'info', message: 'No citations found in document.' });
        setIsWordActionLoading(false);
        return;
      }
      
      // Fetch formatted references from the server API
      const response = await fetch(`${API_BASE}/api/citations/format`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          style: styleName,
          citations: payloadCitations
        })
      });
      
      if (!response.ok) {
        const errText = await response.text();
        let errMsg = errText;
        try {
          const parsedErr = JSON.parse(errText);
          errMsg = parsedErr.detail || errMsg;
        } catch (e) {}
        throw new Error(errMsg);
      }
      
      const data = await response.json();
      
      // Map response citations back to the content controls by ID
      const formattedCitesMap = {};
      data.citations.forEach(cit => {
        formattedCitesMap[cit.id] = cit.text;
      });
      
      // Phase 1: Unlock content controls that will be updated
      await window.Word.run(async (context) => {
        const contentControls = context.document.contentControls;
        contentControls.load('tag, id, cannotEdit');
        await context.sync();
        
        for (let i = 0; i < contentControls.items.length; i++) {
          const cc = contentControls.items[i];
          const ccId = cc.id.toString();
          if (formattedCitesMap[ccId] !== undefined) {
            cc.cannotEdit = false;
          } else if (cc.tag === 'lib-bibliography' && data.bibliography.length > 0) {
            cc.cannotEdit = false;
          }
        }
        await context.sync();
      });
      
      // Phase 2: Update content
      await window.Word.run(async (context) => {
        const contentControls = context.document.contentControls;
        contentControls.load('tag, id, cannotEdit');
        await context.sync();
        
        for (let i = 0; i < contentControls.items.length; i++) {
          const cc = contentControls.items[i];
          const ccId = cc.id.toString();
          if (formattedCitesMap[ccId] !== undefined) {
            const formattedText = formattedCitesMap[ccId] || '';
            cleanAndFormatCitation(cc, formattedText);
          } else if (cc.tag === 'lib-bibliography' && data.bibliography.length > 0) {
            cc.clear();
            const bibText = data.bibliography.join('\n');
            cc.insertText(bibText, window.Word.InsertLocation.replace);
          }
        }
        await context.sync();
      });
      
      // Phase 3: Lock content controls again
      await window.Word.run(async (context) => {
        const contentControls = context.document.contentControls;
        contentControls.load('tag, id, cannotEdit');
        await context.sync();
        
        for (let i = 0; i < contentControls.items.length; i++) {
          const cc = contentControls.items[i];
          const ccId = cc.id.toString();
          if (formattedCitesMap[ccId] !== undefined) {
            cc.cannotEdit = true;
          } else if (cc.tag === 'lib-bibliography' && data.bibliography.length > 0) {
            cc.cannotEdit = true;
          }
        }
        await context.sync();
      });
      
      setWordStatus({ type: 'success', message: 'Citations and bibliography refreshed!' });
      
    } catch (err) {
      console.error("Failed to refresh citations:", err);
      setWordStatus({ type: 'error', message: `Refresh failed: ${err.message || err}` });
    } finally {
      setIsWordActionLoading(false);
    }
  };

  const cleanAndFormatCitation = (cc, text) => {
    let cleanText = text;
    let isSuperscript = false;

    // Check if it's a superscript format like ^(1-3) or ^2
    if (cleanText.includes('^')) {
      isSuperscript = true;
      cleanText = cleanText.replace(/\^/g, '');
      if (cleanText.startsWith('(') && cleanText.endsWith(')')) {
        cleanText = cleanText.substring(1, cleanText.length - 1);
      }
    }

    // Check for unicode superscript characters
    const superscriptMap = {
      '⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
      '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9'
    };
    
    let hasUnicodeSuper = false;
    let decoded = "";
    for (let i = 0; i < cleanText.length; i++) {
      const char = cleanText[i];
      if (superscriptMap[char] !== undefined) {
        decoded += superscriptMap[char];
        hasUnicodeSuper = true;
      } else {
        decoded += char;
      }
    }
    
    if (hasUnicodeSuper) {
      isSuperscript = true;
      cleanText = decoded;
    }

    cc.insertText(cleanText, window.Word.InsertLocation.replace);
    cc.font.superscript = isSuperscript;
  };

  const handleStyleChange = async (newStyle) => {
    setWordStyle(newStyle);
    
    if (window.Office && window.Office.context && window.Office.context.document) {
      try {
        window.Office.context.document.settings.set('citationStyle', newStyle);
        window.Office.context.document.settings.saveAsync();
      } catch (e) {
        console.error("Failed to save style setting:", e);
      }
    }
    
    await handleRefreshCitations(newStyle);
  };

  // Navigation & Tabs
  // activeView can be: 'library', 'notes', 'note-edit', 'untracked', 'duplicates', 'broken'
  const [activeView, setActiveView] = useState('library'); 
  const [libraryFilter, setLibraryFilter] = useState('all'); // 'all', 'pdf', 'ref'

  // Data state
  const [selectedPaper, setSelectedPaper] = useState(null);
  const [paperDetails, setPaperDetails] = useState(null);
  const [notes, setNotes] = useState([]);
  const [selectedNote, setSelectedNote] = useState(null);
  const [folders, setFolders] = useState([]);
  const [renderedCitation, setRenderedCitation] = useState('');
  const [selectedStyle, setSelectedStyle] = useState('nature');

  // Search & Filters state
  const [searchQuery, setSearchQuery] = useState('');
  const [filterAuthor, setFilterAuthor] = useState('');
  const [filterVenue, setFilterVenue] = useState('');
  const [filterYear, setFilterYear] = useState('');
  const [filterDoi, setFilterDoi] = useState('');
  const [searchScope, setSearchScope] = useState('auto'); // 'auto' or 'fulltext'

  // Maintenance lists state
  const [untrackedPapers, setUntrackedPapers] = useState([]);
  const [duplicatesReport, setDuplicatesReport] = useState(null);
  const [brokenPapers, setBrokenPapers] = useState([]);

  // Ingestion Wizard states (unified for upload, untracked ingest, and repair)
  const [showWizardModal, setShowWizardModal] = useState(false);
  const [wizardMode, setWizardMode] = useState('upload'); // 'upload', 'existing', 'repair'
  const [wizardFilePath, setWizardFilePath] = useState(''); // relative path of the existing PDF
  const [wizardRepairPaperId, setWizardRepairPaperId] = useState(''); // paper_id to repair
  const [wizardRepairPaper, setWizardRepairPaper] = useState(null); // full paper object to repair
  const [wizardStep, setWizardStep] = useState(1); // 1: DOI, 2: Semantic Scholar, 3: Manual BibTeX
  const [wizardScanData, setWizardScanData] = useState(null); // holds SHA256, DOI candidate metadata, text_extracted
  const [wizardFolder, setWizardFolder] = useState('');
  const [wizardSearchQuery, setWizardSearchQuery] = useState('');
  const [wizardCandidates, setWizardCandidates] = useState([]);
  const [isSearchingWizard, setIsSearchingWizard] = useState(false);
  const [wizardSelectedCandidate, setWizardSelectedCandidate] = useState(null);
  const [wizardManualBibtex, setWizardManualBibtex] = useState('');
  const [isWizardProcessing, setIsWizardProcessing] = useState(false);

  // Batch Operations states
  const [batchFolder, setBatchFolder] = useState('articles');
  const [batchRecursive, setBatchRecursive] = useState(false);
  const [batchBibtexText, setBatchBibtexText] = useState('');
  const [isBatchDirectoryLoading, setIsBatchDirectoryLoading] = useState(false);
  const [isBatchBibtexLoading, setIsBatchBibtexLoading] = useState(false);
  const [batchDirectoryResult, setBatchDirectoryResult] = useState(null);
  const [batchBibtexResult, setBatchBibtexResult] = useState(null);

  // Sorting & Folder filtering
  const [selectedFolderFilter, setSelectedFolderFilter] = useState('all'); // 'all', '' (root), or folder name
  const [sortField, setSortField] = useState('title');
  const [sortAsc, setSortAsc] = useState(true);

  // Reference add query (reused or kept separate)
  const [showAddRefModal, setShowAddRefModal] = useState(false);
  const [refQuery, setRefQuery] = useState('');
  const [refCandidates, setRefCandidates] = useState([]);
  const [isSearchingRef, setIsSearchingRef] = useState(false);
  const [selectedCandidate, setSelectedCandidate] = useState(null);
  const [manualBibtex, setManualBibtex] = useState('');
  const [showManualInput, setShowManualInput] = useState(false);

  // Edit states
  const [isEditingBibtex, setIsEditingBibtex] = useState(false);
  const [bibtexEditText, setBibtexEditText] = useState('');
  
  // Note editing states
  const [noteContent, setNoteContent] = useState('');
  const [noteTitle, setNoteTitle] = useState('');
  const [noteFilename, setNoteFilename] = useState('');

  // Merge states
  const [showMergeModal, setShowMergeModal] = useState(false);
  const [mergeSearchQuery, setMergeSearchQuery] = useState('');
  const [mergeSelectedPaper, setMergeSelectedPaper] = useState(null);
  const [deleteDropPdf, setDeleteDropPdf] = useState(false);
  
  // Supplement states
  const [showLinkSupplementModal, setShowLinkSupplementModal] = useState(false);
  const [linkSupplementSearchQuery, setLinkSupplementSearchQuery] = useState('');
  const [linkSupplementSelectedParent, setLinkSupplementSelectedParent] = useState(null);

  const [isWizardSupplementMode, setIsWizardSupplementMode] = useState(false);
  const [wizardSupplementSearchQuery, setWizardSupplementSearchQuery] = useState('');
  const [wizardSupplementSelectedParent, setWizardSupplementSelectedParent] = useState(null);
  
  const uploadFileInputRef = useRef(null);

  // Index papers by bibtex_key for fast notes hover lookup
  const papersByBibtexKey = React.useMemo(() => {
    const map = {};
    papers.forEach(p => {
      if (p.bibtex_key) {
        map[p.bibtex_key] = p;
      }
    });
    return map;
  }, [papers]);

  const mergeCandidates = React.useMemo(() => {
    if (!mergeSearchQuery.trim()) return [];
    const tokens = mergeSearchQuery.toLowerCase().split(/\s+/).filter(Boolean);
    if (tokens.length === 0) return [];
    return papers.filter(p => {
      if (p.paper_id === selectedPaper?.paper_id) return false;
      const textToSearch = `${p.title || ''} ${p.authors || ''} ${p.bibtex_key || ''}`.toLowerCase();
      return tokens.every(token => textToSearch.includes(token));
    }).slice(0, 10);
  }, [mergeSearchQuery, papers, selectedPaper]);

  const linkSupplementCandidates = React.useMemo(() => {
    if (!linkSupplementSearchQuery.trim()) return [];
    const tokens = linkSupplementSearchQuery.toLowerCase().split(/\s+/).filter(Boolean);
    if (tokens.length === 0) return [];
    return papers.filter(p => {
      if (p.paper_id === selectedPaper?.paper_id) return false;
      if (p.match_status === 'matched_supplement') return false;
      const textToSearch = `${p.title || ''} ${p.authors || ''} ${p.bibtex_key || ''}`.toLowerCase();
      return tokens.every(token => textToSearch.includes(token));
    }).slice(0, 10);
  }, [linkSupplementSearchQuery, papers, selectedPaper]);

  const wizardSupplementCandidates = React.useMemo(() => {
    if (!wizardSupplementSearchQuery.trim()) return [];
    const tokens = wizardSupplementSearchQuery.toLowerCase().split(/\s+/).filter(Boolean);
    if (tokens.length === 0) return [];
    return papers.filter(p => {
      if (p.match_status === 'matched_supplement') return false;
      const textToSearch = `${p.title || ''} ${p.authors || ''} ${p.bibtex_key || ''}`.toLowerCase();
      return tokens.every(token => textToSearch.includes(token));
    }).slice(0, 10);
  }, [wizardSupplementSearchQuery, papers]);

  const supplements = React.useMemo(() => {
    if (!paperDetails) return [];
    return papers.filter(p => 
      p.match_status === 'matched_supplement' && 
      p.bibtex_key === paperDetails.bibtex_key &&
      p.paper_id !== paperDetails.paper_id
    );
  }, [papers, paperDetails]);

  const escapeHtml = (str) => {
    if (!str) return '';
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  };

  const requestSort = (field) => {
    if (sortField === field) {
      setSortAsc(!sortAsc);
    } else {
      setSortField(field);
      setSortAsc(true);
    }
  };

  const filteredPapers = React.useMemo(() => {
    return papers.filter(p => {
      // Exclude supplements from the main table listing
      if (p.match_status === 'matched_supplement') return false;

      if (selectedFolderFilter === 'all') return true;
      if (!p.pdf_path) return false;
      const parts = p.pdf_path.split('/');
      if (selectedFolderFilter === '') {
        return parts.length === 2;
      }
      return parts.slice(1, -1).join('/') === selectedFolderFilter;
    });
  }, [papers, selectedFolderFilter]);

  const sortedPapers = React.useMemo(() => {
    if (sortField === 'relevance') {
      const sorted = [...filteredPapers];
      sorted.sort((a, b) => {
        const scoreA = a.score || 0;
        const scoreB = b.score || 0;
        return sortAsc ? scoreA - scoreB : scoreB - scoreA;
      });
      return sorted;
    }
    const sorted = [...filteredPapers];
    sorted.sort((a, b) => {
      let valA = a[sortField] || '';
      let valB = b[sortField] || '';
      if (sortField === 'type') {
        valA = a.content_kind || '';
        valB = b.content_kind || '';
      }
      if (sortField === 'year') {
        const numA = parseInt(valA, 10) || 0;
        const numB = parseInt(valB, 10) || 0;
        return sortAsc ? numA - numB : numB - numA;
      }
      valA = String(valA).toLowerCase();
      valB = String(valB).toLowerCase();
      return sortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
    });
    return sorted;
  }, [filteredPapers, sortField, sortAsc]);

  const untrackedInFolder = React.useMemo(() => {
    return untrackedPapers.filter(item => {
      const folderVal = item.folder === '.' ? '' : (item.folder || '');
      return folderVal === wizardFolder;
    });
  }, [untrackedPapers, wizardFolder]);

  // Load initial data
  useEffect(() => {
    fetchPapers();
    fetchNotes();
    fetchFolders();
    fetchMaintenanceCounts();
  }, []);

  // Sync maintenance badges when paper list changes
  useEffect(() => {
    fetchMaintenanceCounts();
  }, [papers]);

  // Reload citation when selected paper or style changes
  useEffect(() => {
    if (selectedPaper) {
      fetchCitation(selectedPaper.paper_id, selectedStyle);
    }
  }, [selectedPaper, selectedStyle]);

  const fetchPapers = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/papers`);
      const data = await res.json();
      setPapers(data);
    } catch (err) {
      console.error('Error fetching papers:', err);
    }
  };

  const fetchFolders = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/folders`);
      const data = await res.json();
      setFolders(data);
      if (data.length > 0) {
        setWizardFolder(data[0]);
      }
    } catch (err) {
      console.error('Error fetching folders:', err);
    }
  };

  const fetchNotes = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/notes`);
      const data = await res.json();
      setNotes(data);
    } catch (err) {
      console.error('Error fetching notes:', err);
    }
  };

  const fetchCitation = async (paperId, style) => {
    try {
      const res = await fetch(`${API_BASE}/api/papers/${paperId}/cite?style=${style}`);
      if (res.ok) {
        const data = await res.json();
        setRenderedCitation(data.citation);
      } else {
        setRenderedCitation('Error generating citation (verify BibTeX metadata).');
      }
    } catch (err) {
      setRenderedCitation('Error generating citation.');
    }
  };

  const fetchMaintenanceCounts = async () => {
    try {
      const untrackedRes = await fetch(`${API_BASE}/api/papers/untracked`);
      if (untrackedRes.ok) {
        const untrackedData = await untrackedRes.json();
        setUntrackedPapers(untrackedData);
      }
      
      const duplicatesRes = await fetch(`${API_BASE}/api/papers/duplicates`);
      if (duplicatesRes.ok) {
        const duplicatesData = await duplicatesRes.json();
        setDuplicatesReport(duplicatesData);
      }
      
      const brokenRes = await fetch(`${API_BASE}/api/papers/broken`);
      if (brokenRes.ok) {
        const brokenData = await brokenRes.json();
        setBrokenPapers(brokenData);
      }
    } catch (err) {
      console.error('Error fetching maintenance counts:', err);
    }
  };

  const handleBatchDirectoryImport = async () => {
    setIsBatchDirectoryLoading(true);
    setBatchDirectoryResult(null);
    try {
      const res = await fetch(`${API_BASE}/api/papers/batch-import-directory`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          directory_path: batchFolder,
          recursive: batchRecursive,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setBatchDirectoryResult(data);
        // Refresh library lists
        fetchPapers();
        fetchFolders();
        fetchMaintenanceCounts();
      } else {
        alert(data.detail || 'Failed to scan and import directory.');
      }
    } catch (err) {
      console.error(err);
      alert('Error scanning and importing directory.');
    } finally {
      setIsBatchDirectoryLoading(false);
    }
  };

  const handleBatchBibtexImport = async () => {
    if (!batchBibtexText.trim()) {
      alert('Please paste some BibTeX entries first.');
      return;
    }
    setIsBatchBibtexLoading(true);
    setBatchBibtexResult(null);
    try {
      const res = await fetch(`${API_BASE}/api/papers/batch-import-bibtex`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          raw_bibtex: batchBibtexText,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setBatchBibtexResult(data);
        // Refresh library lists
        fetchPapers();
        fetchFolders();
        fetchMaintenanceCounts();
      } else {
        alert(data.detail || 'Failed to import BibTeX entries.');
      }
    } catch (err) {
      console.error(err);
      alert('Error importing BibTeX entries.');
    } finally {
      setIsBatchBibtexLoading(false);
    }
  };

  const handleSearch = async () => {
    try {
      const params = new URLSearchParams();
      if (searchQuery) {
        params.append('q', searchQuery);
        setSortField('relevance');
        setSortAsc(false); // Highest score first by default
      } else {
        if (sortField === 'relevance') {
          setSortField('title');
          setSortAsc(true);
        }
      }
      if (filterAuthor) params.append('author', filterAuthor);
      if (filterVenue) params.append('venue', filterVenue);
      if (filterYear) params.append('year', filterYear);
      if (filterDoi) params.append('doi', filterDoi);
      if (libraryFilter === 'pdf') params.append('has_pdf', 'true');
      if (libraryFilter === 'ref') params.append('reference_only', 'true');
      params.append('scope', searchScope);

      const res = await fetch(`${API_BASE}/api/papers/search?${params.toString()}`);
      const data = await res.json();
      setPapers(data);
    } catch (err) {
      console.error('Search failed:', err);
    }
  };

  // Trigger search on filter/scope change or search query change
  useEffect(() => {
    handleSearch();
  }, [searchQuery, libraryFilter, filterAuthor, filterVenue, filterYear, filterDoi, searchScope]);

  const selectPaper = async (paper) => {
    setSelectedPaper(paper);
    setIsEditingBibtex(false);
    try {
      const res = await fetch(`${API_BASE}/api/papers/${paper.paper_id}`);
      const data = await res.json();
      setPaperDetails(data);
      setBibtexEditText(data.raw_bibtex || '');
    } catch (err) {
      console.error('Error loading details:', err);
    }
  };

  const handleUpdateBibtex = async () => {
    if (!selectedPaper) return;
    try {
      const res = await fetch(`${API_BASE}/api/papers/${selectedPaper.paper_id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ raw_bibtex: bibtexEditText })
      });
      if (res.ok) {
        setIsEditingBibtex(false);
        // Refresh paper lists
        fetchPapers();
        selectPaper(selectedPaper);
      } else {
        const err = await res.json();
        alert(`Failed to update: ${err.detail}`);
      }
    } catch (err) {
      alert(`Error updating: ${err}`);
    }
  };

  const handleDeletePaper = async (deletePdfFile) => {
    if (!selectedPaper) return;
    if (!confirm('Are you sure you want to delete this record? This action cannot be undone.')) return;

    try {
      const res = await fetch(`${API_BASE}/api/papers/${selectedPaper.paper_id}?delete_pdf=${deletePdfFile}`, {
        method: 'DELETE'
      });
      if (res.ok) {
        setSelectedPaper(null);
        setPaperDetails(null);
        fetchPapers();
      } else {
        const err = await res.json();
        alert(`Failed to delete: ${err.detail}`);
      }
    } catch (err) {
      alert(`Error deleting: ${err}`);
    }
  };

  const closeWizardModal = () => {
    setShowWizardModal(false);
    setWizardScanData(null);
    setWizardSelectedCandidate(null);
    setWizardCandidates([]);
    setWizardManualBibtex('');
    setWizardRepairPaper(null);
    setWizardRepairPaperId('');
    setIsWizardSupplementMode(false);
    setWizardSupplementSearchQuery('');
    setWizardSupplementSelectedParent(null);
  };

  // Ingestion Wizard Trigger Functions
  const startWizardExistingSelector = () => {
    // Refresh untracked papers list when opening selector
    fetchMaintenanceCounts();
    setWizardMode('existing');
    setWizardStep(0);
    setWizardFilePath('');
    setWizardScanData(null);
    setWizardManualBibtex('');
    setWizardSelectedCandidate(null);
    setWizardCandidates([]);
    setWizardFolder(folders[0] || '');
    setShowWizardModal(true);
    setIsWizardProcessing(false);
  };

  const handleWizardScanPdf = async () => {
    if (!wizardFilePath) return;
    setIsWizardProcessing(true);
    setWizardScanData(null);
    setWizardManualBibtex('');
    setWizardSelectedCandidate(null);
    setWizardCandidates([]);

    try {
      const res = await fetch(`${API_BASE}/api/papers/scan-pdf-metadata?path=${encodeURIComponent(wizardFilePath)}`);
      if (res.ok) {
        const data = await res.json();
        setWizardScanData(data);
        if (data.is_duplicate) {
          return;
        }
        setWizardSearchQuery(data.title_query || '');
        if (data.doi_bibtex) {
          setWizardManualBibtex(data.doi_bibtex);
        }
        if (data.doi_found) {
          setWizardStep(1);
        } else {
          alert('No DOI found in PDF. Moving to Semantic Scholar search.');
          setWizardStep(2);
        }
      } else {
        alert('Scanning PDF failed. Proceeding to Semantic Scholar search.');
        setWizardStep(2);
      }
    } catch (err) {
      alert(`Error scanning: ${err}`);
      setWizardStep(2);
    } finally {
      setIsWizardProcessing(false);
    }
  };

  const startWizardExisting = async (relativePath) => {
    setWizardMode('existing');
    setWizardStep(1);
    setWizardFilePath(relativePath);
    setWizardScanData(null);
    setWizardManualBibtex('');
    setWizardSelectedCandidate(null);
    setWizardCandidates([]);
    setShowWizardModal(true);
    setIsWizardProcessing(true);

    try {
      const res = await fetch(`${API_BASE}/api/papers/scan-pdf-metadata?path=${encodeURIComponent(relativePath)}`);
      if (res.ok) {
        const data = await res.json();
        setWizardScanData(data);
        if (data.is_duplicate) {
          return;
        }
        setWizardSearchQuery(data.title_query || '');
        if (data.doi_bibtex) {
          setWizardManualBibtex(data.doi_bibtex);
        }
        if (!data.doi_found) {
          setWizardStep(2);
        }
      } else {
        alert('Scanning PDF failed. Proceeding to search candidates.');
        setWizardStep(2);
      }
    } catch (err) {
      alert(`Error scanning: ${err}`);
      setWizardStep(2);
    } finally {
      setIsWizardProcessing(false);
    }
  };

  const startWizardRepair = async (paper) => {
    setWizardMode('repair');
    setWizardStep(1);
    setWizardRepairPaperId(paper.paper_id);
    setWizardRepairPaper(paper);
    setWizardManualBibtex('');
    setWizardSelectedCandidate(null);
    setWizardCandidates([]);
    
    if (paper.pdf_path) {
      setWizardFilePath(paper.pdf_path);
      setWizardScanData(null);
      setShowWizardModal(true);
      setIsWizardProcessing(true);
      try {
        const res = await fetch(`${API_BASE}/api/papers/scan-pdf-metadata?path=${encodeURIComponent(paper.pdf_path)}&exclude_paper_id=${encodeURIComponent(paper.paper_id)}`);
        if (res.ok) {
          const data = await res.json();
          setWizardScanData(data);
          setWizardSearchQuery(data.title_query || paper.title || '');
          if (data.doi_bibtex) {
            setWizardManualBibtex(data.doi_bibtex);
          }
          if (!data.doi_found) {
            setWizardStep(2);
          }
        } else {
          setWizardSearchQuery(paper.title || '');
          setWizardStep(2);
        }
      } catch (err) {
        setWizardSearchQuery(paper.title || '');
        setWizardStep(2);
      } finally {
        setIsWizardProcessing(false);
      }
    } else {
      setWizardSearchQuery(paper.title || '');
      setWizardScanData(null);
      setWizardStep(2);
      setShowWizardModal(true);
    }
  };

  const handleWizardSearch = async () => {
    if (!wizardSearchQuery.trim()) return;
    setIsSearchingWizard(true);
    setWizardCandidates([]);
    setWizardSelectedCandidate(null);
    try {
      const res = await fetch(`${API_BASE}/api/candidates?q=${encodeURIComponent(wizardSearchQuery)}`);
      if (res.ok) {
        const data = await res.json();
        setWizardCandidates(data);
        if (data.length > 0) {
          handleWizardSelectCandidate(data[0]);
        }
      } else {
        alert('Failed to search candidates. Try manual BibTeX.');
        setWizardStep(3);
      }
    } catch (err) {
      alert(`Error: ${err}`);
      setWizardStep(3);
    } finally {
      setIsSearchingWizard(false);
    }
  };

  const handleWizardSelectCandidate = async (candidate) => {
    setWizardSelectedCandidate(candidate);
    setIsWizardProcessing(true);
    try {
      const res = await fetch(`${API_BASE}/api/candidates/bibtex`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ selected_candidate: candidate })
      });
      if (res.ok) {
        const data = await res.json();
        setWizardManualBibtex(data.bibtex);
      }
    } catch (err) {
      console.error('Failed to pre-format candidate as BibTeX:', err);
    } finally {
      setIsWizardProcessing(false);
    }
  };

  const handleWizardConfirm = async () => {
    let body = {};
    if (wizardStep === 1 || wizardStep === 3) {
      if (!wizardManualBibtex.trim()) {
        alert('BibTeX content is empty.');
        return;
      }
      body.manual_bibtex = wizardManualBibtex;
    } else if (wizardStep === 2) {
      if (wizardManualBibtex.trim()) {
        body.manual_bibtex = wizardManualBibtex;
      } else if (wizardSelectedCandidate) {
        body.selected_candidate = wizardSelectedCandidate;
      } else {
        alert('Please select a candidate or paste BibTeX.');
        return;
      }
    }

    setIsWizardProcessing(true);

    try {
      let url = '';
      let method = 'POST';
      
      if (wizardMode === 'upload') {
        url = `${API_BASE}/api/papers/upload-confirm`;
        body.temp_filename = wizardScanData?.temp_filename;
        body.folder = wizardFolder;
        body.title_query = wizardScanData?.title_query || '';
      } else if (wizardMode === 'existing') {
        url = `${API_BASE}/api/papers/ingest-existing`;
        body.relative_path = wizardFilePath;
      } else if (wizardMode === 'repair') {
        url = `${API_BASE}/api/papers/${wizardRepairPaperId}`;
        method = 'PUT';
        body = { raw_bibtex: wizardManualBibtex };
      }

      const res = await fetch(url, {
        method: method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });

      if (res.ok) {
        const data = await res.json();
        closeWizardModal();
        fetchPapers();
        fetchMaintenanceCounts();
        if (data.data) {
          selectPaper(data.data);
        } else if (wizardMode === 'repair') {
          if (selectedPaper?.paper_id === wizardRepairPaperId) {
            selectPaper(selectedPaper);
          }
        }
        if (data.status === 'duplicate') {
          alert(data.message || 'This paper is already in your library. The duplicate PDF file was deleted from disk.');
        } else {
          alert('Operation completed successfully!');
        }
      } else {
        const err = await res.json();
        alert(`Failed: ${err.detail}`);
      }
    } catch (err) {
      alert(`Error: ${err}`);
    } finally {
      setIsWizardProcessing(false);
    }
  };

  const handleViewExistingDuplicate = () => {
    const existingPaperId = wizardScanData?.existing_paper_id;
    closeWizardModal();
    setActiveView('library');
    if (existingPaperId) {
      selectPaper({ paper_id: existingPaperId });
    }
  };

  const handleResolveDuplicate = async (action) => {
    setIsWizardProcessing(true);
    try {
      const res = await fetch(`${API_BASE}/api/papers/resolve-untracked-duplicate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          existing_paper_id: wizardScanData.existing_paper_id,
          relative_path: wizardFilePath,
          action: action
        })
      });

      if (res.ok) {
        const data = await res.json();
        closeWizardModal();
        fetchMaintenanceCounts();
        fetchPapers();
        
        if (action === 'use_new_path' && data.data) {
          setActiveView('library');
          selectPaper(data.data);
        }
        
        alert(data.message || 'Operation completed successfully!');
      } else {
        const err = await res.json();
        alert(`Failed: ${err.detail}`);
      }
    } catch (err) {
      alert(`Error: ${err}`);
    } finally {
      setIsWizardProcessing(false);
    }
  };

  const handleLinkSupplement = async () => {
    if (!selectedPaper || !linkSupplementSelectedParent) return;
    setIsWizardProcessing(true);
    try {
      const res = await fetch(`${API_BASE}/api/papers/${selectedPaper.paper_id}/link-supplement`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ parent_paper_id: linkSupplementSelectedParent.paper_id })
      });
      if (res.ok) {
        setShowLinkSupplementModal(false);
        setLinkSupplementSearchQuery('');
        setLinkSupplementSelectedParent(null);
        fetchPapers();
        selectPaper(selectedPaper);
        alert('Linked paper as a supplement successfully!');
      } else {
        const err = await res.json();
        alert(`Failed to link: ${err.detail}`);
      }
    } catch (err) {
      alert(`Error linking: ${err}`);
    } finally {
      setIsWizardProcessing(false);
    }
  };

  const handleWizardIngestAsSupplement = async () => {
    if (!wizardFilePath || !wizardSupplementSelectedParent) return;
    setIsWizardProcessing(true);
    try {
      const res = await fetch(`${API_BASE}/api/papers/ingest-as-supplement`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          relative_path: wizardFilePath,
          parent_paper_id: wizardSupplementSelectedParent.paper_id
        })
      });

      if (res.ok) {
        const data = await res.json();
        closeWizardModal();
        fetchMaintenanceCounts();
        fetchPapers();
        
        if (data.data) {
          selectPaper(data.data);
        }
        alert('Ingested and linked as supplement successfully!');
      } else {
        const err = await res.json();
        alert(`Failed: ${err.detail}`);
      }
    } catch (err) {
      alert(`Error: ${err}`);
    } finally {
      setIsWizardProcessing(false);
    }
  };

  // Add Reference Only Flow
  const handleSearchRef = async () => {
    if (!refQuery.trim()) return;
    setIsSearchingRef(true);
    setRefCandidates([]);
    setSelectedCandidate(null);
    setManualBibtex('');
    setShowManualInput(false);

    try {
      const res = await fetch(`${API_BASE}/api/candidates?q=${encodeURIComponent(refQuery)}`);
      if (res.ok) {
        const data = await res.json();
        setRefCandidates(data);
        if (data.length > 0) {
          setSelectedCandidate(data[0]);
        }
      } else {
        alert('Failed to search Semantic Scholar. Try manual BibTeX.');
        setShowManualInput(true);
      }
    } catch (err) {
      alert(`Error searching: ${err}`);
      setShowManualInput(true);
    } finally {
      setIsSearchingRef(false);
    }
  };
  // For now, let's finish App.jsx draft.

  // Note management
  const selectNote = async (note) => {
    setActiveView('note-edit');
    setNoteFilename(note.filename);
    try {
      const res = await fetch(`${API_BASE}/api/notes/${note.filename}`);
      const data = await res.json();
      setNoteContent(data.content || '');
      setNoteTitle(note.title || note.filename);
    } catch (err) {
      console.error(err);
    }
  };

  const handleCreateNote = () => {
    setActiveView('note-edit');
    setNoteFilename('');
    setNoteTitle('Untitled Note');
    setNoteContent('# Untitled Note\n\nWrite your thoughts here. Cite items using `@bibtex_key` (e.g. `@brandani2013quantifying`).');
  };

  const handleMovePaper = async (destFolder) => {
    if (!selectedPaper) return;
    try {
      const res = await fetch(`${API_BASE}/api/papers/${selectedPaper.paper_id}/move`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ destination: destFolder })
      });
      if (res.ok) {
        fetchFolders();
        fetchPapers();
        selectPaper(selectedPaper);
        alert('Paper moved successfully!');
      } else {
        const err = await res.json();
        alert(`Failed to move: ${err.detail}`);
      }
    } catch (err) {
      alert(`Error moving paper: ${err}`);
    }
  };

  const handleSaveNote = async () => {
    if (!noteTitle.trim()) {
      alert('Please enter a note title.');
      return;
    }
    const newFilename = noteTitle.toLowerCase().replace(/[^a-z0-9]+/g, '-') + '.md';
    const oldFilename = noteFilename;

    try {
      const res = await fetch(`${API_BASE}/api/notes/${newFilename}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: noteContent })
      });
      if (res.ok) {
        const data = await res.json();
        setNoteFilename(data.filename);
        if (oldFilename && oldFilename !== newFilename) {
          await fetch(`${API_BASE}/api/notes/${oldFilename}`, {
            method: 'DELETE'
          });
        }
        fetchNotes();
        alert('Note saved!');
      } else {
        alert('Failed to save note.');
      }
    } catch (err) {
      alert(`Error saving: ${err}`);
    }
  };

  const handleDeleteNote = async (filename, e) => {
    e.stopPropagation();
    if (!confirm('Are you sure you want to delete this note?')) return;
    try {
      const res = await fetch(`${API_BASE}/api/notes/${filename}`, {
        method: 'DELETE'
      });
      if (res.ok) {
        fetchNotes();
        if (noteFilename === filename) {
          setActiveView('library');
          setNoteFilename('');
          setNoteContent('');
        }
      }
    } catch (err) {
      console.error(err);
    }
  };

  // Compile markdown note and resolve pandoc citations
  const renderNotePreview = (markdown) => {
    if (!markdown) return '';
    // A basic markdown parser that escapes HTML and parses:
    // Headers, Bold, Lists, Code, Line breaks, Pandoc citations
    let html = markdown
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    // Headings
    html = html.replace(/^# (.*$)/gim, '<h1>$1</h1>');
    html = html.replace(/^## (.*$)/gim, '<h2>$1</h2>');
    html = html.replace(/^### (.*$)/gim, '<h3>$1</h3>');
    
    // Bold & Italics
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');
    
    // Blockquotes
    html = html.replace(/^\> (.*$)/gim, '<blockquote>$1</blockquote>');
    
    // Code blocks
    html = html.replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>');
    html = html.replace(/`(.*?)`/g, '<code>$1</code>');

    // List items
    html = html.replace(/^\s*-\s+(.*$)/gim, '<ul><li>$1</li></ul>');
    html = html.replace(/^\s*\*\s+(.*$)/gim, '<ul><li>$1</li></ul>');
    // Group consecutive <ul> tags
    html = html.replace(/<\/ul>\s*<ul>/g, '');

    // Line breaks
    html = html.replace(/\n\n/g, '<p></p>');

    // Resolve Pandoc citations: `@brandani2013quantifying`
    // Citekey can contain alpha, digits, underscores, dashes, colons.
    const citationRegex = /@([A-Za-z0-9_:\-]+)/g;
    html = html.replace(citationRegex, (match, citekey) => {
      const paper = papersByBibtexKey[citekey];
      if (paper) {
        const title = escapeHtml(paper.title || 'Unknown Title');
        const authors = escapeHtml(paper.authors || 'Unknown Authors');
        const abstract = escapeHtml(paper.abstract || 'No abstract available.');
        const venue = escapeHtml(paper.venue || 'Unknown Venue');
        const year = escapeHtml(paper.year || 'n/a');
        
        return `
          <span class="citation-link" onclick="window.selectPaperFromNote('${paper.paper_id}')">
            ${match}
            <span class="citation-hover-card">
              <span class="hover-card-title">${title}</span>
              <span class="hover-card-authors">${authors}</span>
              <span class="hover-card-abstract">${abstract}</span>
              <span class="hover-card-meta">
                <span>${venue}</span>
                <span>${year}</span>
              </span>
            </span>
          </span>
        `;
      }
      return match;
    });

    return html;
  };

  // Expose function to window so compiled note onClick can trigger it
  useEffect(() => {
    window.selectPaperFromNote = (paperId) => {
      const paper = papers.find(p => p.paper_id === paperId);
      if (paper) {
        setActiveView('library');
        selectPaper(paper);
      }
    };
    return () => {
      delete window.selectPaperFromNote;
    };
  }, [papers]);

  if (isWordAddin) {
    if (!isOfficeReady) {
      return (
        <div className="word-loading-overlay">
          <div className="word-spinner"></div>
          <p>Initializing Microsoft Word Integration...</p>
        </div>
      );
    }

    return (
      <div className="word-pane-container">
        {/* Header */}
        <div className="word-header">
          <div className="word-header-title">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--accent-text)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1-2.5-2.5Z"></path><path d="M6 6h10"></path><path d="M6 10h10"></path></svg>
            <h2>Literature Library</h2>
          </div>
          
          <div className="word-style-selector">
            <label htmlFor="citation-style-select">Citation Style</label>
            <select 
              id="citation-style-select" 
              className="word-select"
              value={wordStyle} 
              onChange={(e) => handleStyleChange(e.target.value)}
              disabled={isWordActionLoading}
            >
              <option value="nature">Nature</option>
              <option value="ieee">IEEE</option>
              <option value="nar">Nucleic Acids Research (NAR)</option>
              <option value="nlm">NLM Citation Sequence</option>
              <option value="acs">ACS</option>
            </select>
          </div>
        </div>

        {/* Body */}
        <div className="word-body">
          <div className="word-search-container">
            <input 
              type="text" 
              className="word-search-input" 
              placeholder="Search library..." 
              value={wordSearchQuery}
              onChange={(e) => setWordSearchQuery(e.target.value)}
              disabled={isWordActionLoading}
            />
          </div>

          {/* Selected References Widget */}
          {selectedPapersDetails.length > 0 && (
            <div className="word-selected-references">
              <div className="word-selected-references-header">
                <span>Selected ({selectedPapersDetails.length})</span>
                <button 
                  className="word-clear-selected-btn"
                  onClick={() => setSelectedWordPaperIds([])}
                >
                  Clear All
                </button>
              </div>
              <div className="word-selected-references-list">
                {selectedPapersDetails.map(paper => (
                  <div key={paper.paper_id} className="word-selected-reference-item">
                    <span className="word-selected-reference-title" title={paper.title}>
                      {paper.title}
                    </span>
                    <button 
                      className="word-selected-reference-remove-btn"
                      onClick={() => setSelectedWordPaperIds(selectedWordPaperIds.filter(id => id !== paper.paper_id))}
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="word-search-results">
            {displayedWordPapers.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '20px', color: 'var(--text-secondary)' }}>
                No papers found.
              </div>
            ) : (
              displayedWordPapers.map(paper => {
                const isSelected = selectedWordPaperIds.includes(paper.paper_id);
                return (
                  <div 
                    key={paper.paper_id} 
                    className={`word-paper-card ${isSelected ? 'selected' : ''}`}
                    onClick={() => {
                      if (isSelected) {
                        setSelectedWordPaperIds(selectedWordPaperIds.filter(id => id !== paper.paper_id));
                      } else {
                        setSelectedWordPaperIds([...selectedWordPaperIds, paper.paper_id]);
                      }
                    }}
                  >
                    <input 
                      type="checkbox" 
                      className="word-paper-card-checkbox"
                      checked={isSelected}
                      onChange={() => {}} // handled by parent onClick
                    />
                    <div className="word-paper-card-details">
                      <div className="word-paper-title">{paper.title}</div>
                      <div className="word-paper-authors">{paper.authors}</div>
                      <div className="word-paper-meta">
                        {paper.year} — {paper.venue}
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Footer Status */}
        {wordStatus && (
          <div style={{ padding: '0 15px' }}>
            <div className={`word-status word-status-${wordStatus.type}`}>
              {wordStatus.message}
            </div>
          </div>
        )}

        {/* Footer Buttons */}
        <div className="word-footer">
          <button 
            className="word-btn word-btn-primary"
            onClick={handleInsertCitation}
            disabled={selectedWordPaperIds.length === 0 || isWordActionLoading}
          >
            Insert Citation ({selectedWordPaperIds.length})
          </button>
          
          <div className="word-btn-row">
            <button 
              className="word-btn word-btn-secondary"
              onClick={handleInsertBibliography}
              disabled={isWordActionLoading}
            >
              Add Bibliography
            </button>
            <button 
              className="word-btn word-btn-secondary"
              onClick={() => handleRefreshCitations(wordStyle)}
              disabled={isWordActionLoading}
            >
              {isWordActionLoading ? 'Refreshing...' : 'Refresh All'}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={`app-container ${selectedPaper ? 'with-details' : ''}`}>
      {/* 1. Left Sidebar */}
      <div className="sidebar">
        <div className="sidebar-header">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="var(--accent-color)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1-2.5-2.5Z"></path><path d="M6 6h10"></path><path d="M6 10h10"></path></svg>
          <h2>Literature Library</h2>
        </div>
        
        <div className="sidebar-menu">
          <span className="sidebar-label">Library</span>
          <a className={`menu-item ${activeView === 'library' && libraryFilter === 'all' ? 'active' : ''}`} onClick={() => { setActiveView('library'); setLibraryFilter('all'); }}>
            All Items
          </a>
          <a className={`menu-item ${activeView === 'library' && libraryFilter === 'pdf' ? 'active' : ''}`} onClick={() => { setActiveView('library'); setLibraryFilter('pdf'); }}>
            PDF-Backed
          </a>
          <a className={`menu-item ${activeView === 'library' && libraryFilter === 'ref' ? 'active' : ''}`} onClick={() => { setActiveView('library'); setLibraryFilter('ref'); }}>
            Reference-Only
          </a>
        </div>

        <div className="sidebar-menu" style={{ borderTop: '1px solid var(--border-color)', paddingTop: '15px' }}>
          <span className="sidebar-label">Maintenance</span>
          <a className={`menu-item ${activeView === 'untracked' ? 'active' : ''}`} onClick={() => { setActiveView('untracked'); setSelectedPaper(null); }}>
            Untracked PDFs
            {untrackedPapers.length > 0 && <span className="badge badge-warning" style={{ marginLeft: 'auto', backgroundColor: 'var(--accent-color)', color: 'var(--text-primary)', padding: '2px 6px', borderRadius: '10px', fontSize: '11px', fontWeight: 'bold' }}>{untrackedPapers.length}</span>}
          </a>
          <a className={`menu-item ${activeView === 'duplicates' ? 'active' : ''}`} onClick={() => { setActiveView('duplicates'); setSelectedPaper(null); }}>
            Duplicates Clusters
            {duplicatesReport?.duplicate_components && duplicatesReport.duplicate_components.length > 0 && (
              <span className="badge badge-danger" style={{ marginLeft: 'auto', backgroundColor: 'var(--danger-color)', color: '#fff', padding: '2px 6px', borderRadius: '10px', fontSize: '11px', fontWeight: 'bold' }}>
                {duplicatesReport.duplicate_components.length}
              </span>
            )}
          </a>
          <a className={`menu-item ${activeView === 'broken' ? 'active' : ''}`} onClick={() => { setActiveView('broken'); setSelectedPaper(null); }}>
            Broken Entries
            {brokenPapers.length > 0 && <span className="badge badge-warning" style={{ marginLeft: 'auto', backgroundColor: 'var(--danger-color)', color: '#fff', padding: '2px 6px', borderRadius: '10px', fontSize: '11px', fontWeight: 'bold' }}>{brokenPapers.length}</span>}
          </a>
          <a className={`menu-item ${activeView === 'batch' ? 'active' : ''}`} onClick={() => { setActiveView('batch'); setSelectedPaper(null); }}>
            Batch Operations
          </a>
        </div>

        <div className="sidebar-notes-section">
          <span className="sidebar-label">Notes</span>
          <button className="new-note-btn" onClick={handleCreateNote}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M12 5v14M5 12h14"></path></svg>
            New Note
          </button>
          
          <div className="notes-list">
            {notes.map(note => (
              <div 
                key={note.filename} 
                className={`note-item ${activeView === 'note-edit' && noteFilename === note.filename ? 'active' : ''}`}
                onClick={() => selectNote(note)}
              >
                <span className="note-title">{note.title}</span>
                <button className="note-delete-btn" onClick={(e) => handleDeleteNote(note.filename, e)}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M10 11v6M14 11v6"></path></svg>
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* 2. Center Content Pane */}
      <div className="center-pane">
        {activeView === 'library' ? (
          <>
            {/* Navbar */}
            <div className="top-navbar">
              <div className="search-container">
                <div className="search-input-wrapper">
                  <svg className="search-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"></circle><path d="m21 21-4.3-4.3"></path></svg>
                  <input 
                    type="text" 
                    className="search-input" 
                    placeholder="Search library..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
                  />
                </div>
                <select 
                  className="select-style" 
                  value={searchScope}
                  onChange={(e) => setSearchScope(e.target.value)}
                  style={{ height: '36px' }}
                >
                  <option value="auto">Metadata Search</option>
                  <option value="fulltext">Full Text Search</option>
                </select>
                <button className="btn" onClick={handleSearch}>Search</button>
              </div>

              <div className="actions-container">
                <button className="btn btn-primary" onClick={startWizardExistingSelector}>
                  Add PDF Paper
                </button>
                <button className="btn" onClick={() => {
                  setWizardMode('ref');
                  setWizardStep(2);
                  setWizardSearchQuery('');
                  setWizardCandidates([]);
                  setWizardSelectedCandidate(null);
                  setWizardManualBibtex('');
                  setShowWizardModal(true);
                }}>
                  Add Ref-Only
                </button>
              </div>
            </div>

            {/* Advanced Filters */}
            <div className="filters-bar">
              <div className="filter-group">
                <label>Author</label>
                <input type="text" className="filter-input" placeholder="e.g. Einstein" value={filterAuthor} onChange={e => setFilterAuthor(e.target.value)} />
              </div>
              <div className="filter-group">
                <label>Venue</label>
                <input type="text" className="filter-input" placeholder="e.g. Nature" value={filterVenue} onChange={e => setFilterVenue(e.target.value)} />
              </div>
              <div className="filter-group">
                <label>Year</label>
                <input type="text" className="filter-input" placeholder="e.g. 2023" style={{ width: '80px' }} value={filterYear} onChange={e => setFilterYear(e.target.value)} />
              </div>
              <div className="filter-group">
                <label>DOI</label>
                <input type="text" className="filter-input" placeholder="e.g. 10.1038" value={filterDoi} onChange={e => setFilterDoi(e.target.value)} />
              </div>
              <div className="filter-group">
                <label>Folder</label>
                <select 
                  className="filter-input" 
                  value={selectedFolderFilter} 
                  onChange={e => setSelectedFolderFilter(e.target.value)}
                  style={{ height: '28px' }}
                >
                  <option value="all">All Folders</option>
                  <option value="">(root)</option>
                  {folders.map(f => (
                    <option key={f} value={f}>{f}</option>
                  ))}
                </select>
              </div>
            </div>

            {/* Table Paper list */}
            <div className="list-container">
              {sortedPapers.length === 0 ? (
                <div className="empty-state">
                  <span className="empty-state-icon">📚</span>
                  <p>No papers found matching filters.</p>
                </div>
              ) : (
                <table className="table">
                  <thead>
                    <tr>
                      {searchQuery && (
                        <th onClick={() => requestSort('relevance')} style={{ cursor: 'pointer', userSelect: 'none', width: '90px' }}>
                          Relevance {sortField === 'relevance' && (sortAsc ? '▲' : '▼')}
                        </th>
                      )}
                      <th onClick={() => requestSort('title')} style={{ cursor: 'pointer', userSelect: 'none' }}>
                        Title {sortField === 'title' && (sortAsc ? '▲' : '▼')}
                      </th>
                      <th onClick={() => requestSort('authors')} style={{ cursor: 'pointer', userSelect: 'none' }}>
                        Authors {sortField === 'authors' && (sortAsc ? '▲' : '▼')}
                      </th>
                      <th onClick={() => requestSort('year')} style={{ cursor: 'pointer', userSelect: 'none' }}>
                        Year {sortField === 'year' && (sortAsc ? '▲' : '▼')}
                      </th>
                      <th onClick={() => requestSort('venue')} style={{ cursor: 'pointer', userSelect: 'none' }}>
                        Venue {sortField === 'venue' && (sortAsc ? '▲' : '▼')}
                      </th>
                      <th onClick={() => requestSort('type')} style={{ cursor: 'pointer', userSelect: 'none' }}>
                        Type {sortField === 'type' && (sortAsc ? '▲' : '▼')}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedPapers.map(paper => (
                      <tr 
                        key={paper.paper_id} 
                        className={selectedPaper?.paper_id === paper.paper_id ? 'selected' : ''}
                        onClick={() => selectPaper(paper)}
                      >
                        {searchQuery && (
                          <td style={{ fontSize: '12px', color: 'var(--text-secondary)' }} title={paper.match_explanation}>
                            {paper.score ? paper.score.toFixed(1) : '-'}
                          </td>
                        )}
                        <td className="paper-title-cell" title={paper.title}>{paper.title}</td>
                        <td className="paper-authors-cell" title={paper.authors}>{paper.authors}</td>
                        <td>{paper.year}</td>
                        <td>{paper.venue || 'n/a'}</td>
                        <td>
                          <span className={`kind-badge ${paper.content_kind === 'pdf_backed' ? 'kind-pdf' : 'kind-ref'}`}>
                            {paper.content_kind === 'pdf_backed' ? 'PDF' : 'Ref'}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </>
        ) : activeView === 'untracked' ? (
          <div className="maintenance-view" style={{ padding: '20px', overflowY: 'auto', height: '100%' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px', borderBottom: '1px solid var(--border-color)', paddingBottom: '10px' }}>
              <h2>Untracked PDFs in articles/</h2>
              <button className="btn" onClick={fetchMaintenanceCounts}>Refresh Scan</button>
            </div>
            {untrackedPapers.length === 0 ? (
              <div className="empty-state">
                <span className="empty-state-icon">✓</span>
                <p>No untracked PDFs found in the articles directory!</p>
              </div>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Filename</th>
                    <th>Folder Location</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {untrackedPapers.map(item => (
                    <tr key={item.relative_path}>
                      <td style={{ fontWeight: '500' }}>{item.filename}</td>
                      <td style={{ color: 'var(--text-secondary)' }}>articles/{item.folder || '(root)'}</td>
                      <td>
                        <button className="btn btn-primary" style={{ padding: '4px 10px', fontSize: '12.5px' }} onClick={() => startWizardExisting(item.relative_path)}>
                          Ingest File
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        ) : activeView === 'duplicates' ? (
          <div className="maintenance-view" style={{ padding: '20px', overflowY: 'auto', height: '100%' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px', borderBottom: '1px solid var(--border-color)', paddingBottom: '10px' }}>
              <h2>Likely Duplicate Clusters</h2>
              <button className="btn" onClick={fetchMaintenanceCounts}>Refresh Audit</button>
            </div>
            {!duplicatesReport?.duplicate_components || duplicatesReport.duplicate_components.length === 0 ? (
              <div className="empty-state">
                <span className="empty-state-icon">✓</span>
                <p>No duplicate records found in your library index!</p>
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
                {duplicatesReport.duplicate_components.map((component, compIdx) => {
                  const compReport = duplicatesReport.component_reports[compIdx];
                  return (
                    <div key={compIdx} style={{ border: '1px solid var(--border-color)', borderRadius: '8px', padding: '15px', backgroundColor: 'var(--bg-secondary)' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
                        <h4 style={{ color: 'var(--danger-color)', textTransform: 'capitalize' }}>
                          Cluster #{compIdx + 1}: {compReport.classification.replace(/_/g, ' ')}
                        </h4>
                        <span style={{ fontSize: '12px', color: 'var(--text-secondary)', maxWidth: '400px', textAlign: 'right' }}>
                          {compReport.recommendation}
                        </span>
                      </div>
                      
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                        {compReport.rows.map((row, rowIdx) => {
                          const isPreferred = row.paper_id === compReport.preferred_keep_paper_id;
                          return (
                            <div key={rowIdx} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px', borderRadius: '6px', border: isPreferred ? '1px solid var(--success-color)' : '1px dashed var(--border-color)', backgroundColor: isPreferred ? 'rgba(5, 150, 105, 0.03)' : 'var(--bg-primary)' }}>
                              <div>
                                <div style={{ fontWeight: '600' }}>
                                  {row.title} {isPreferred && <span style={{ color: 'var(--success-color)', fontSize: '11px', marginLeft: '5px' }}>[Recommended Keep]</span>}
                                </div>
                                <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                                  {row.authors} ({row.year}) — {row.bibtex_key || '(no BibTeX key)'}
                                </div>
                                <div style={{ fontSize: '11px', color: 'var(--accent-color)' }}>
                                  Path: {row.pdf_path || 'Reference Only'}
                                </div>
                              </div>
                              {!isPreferred && (
                                <div style={{ display: 'flex', gap: '8px' }}>
                                  <button 
                                    className="btn"
                                    style={{ padding: '4px 10px', fontSize: '12px', backgroundColor: '#e0f2fe', borderColor: '#bae6fd', color: '#0369a1' }}
                                    onClick={async () => {
                                      const preferredPaper = compReport.rows.find(r => r.paper_id === compReport.preferred_keep_paper_id);
                                      if (confirm(`Link "${row.title}" as a supplement to "${preferredPaper?.title}"?`)) {
                                        try {
                                          const res = await fetch(`${API_BASE}/api/papers/${row.paper_id}/link-supplement`, {
                                            method: 'POST',
                                            headers: { 'Content-Type': 'application/json' },
                                            body: JSON.stringify({
                                              parent_paper_id: compReport.preferred_keep_paper_id
                                            })
                                          });
                                          if (res.ok) {
                                            fetchPapers();
                                            fetchMaintenanceCounts();
                                            alert('Linked as supplement successfully!');
                                          }
                                        } catch (err) {
                                          alert(err);
                                        }
                                      }
                                    }}
                                  >
                                    Link as Supplement
                                  </button>
                                  <button 
                                    className="btn" 
                                    style={{ padding: '4px 10px', fontSize: '12px', color: 'var(--danger-color)', borderColor: 'var(--danger-color)' }}
                                    onClick={async () => {
                                      if (confirm(`Merge "${row.title}" into "${compReport.rows.find(r => r.paper_id === compReport.preferred_keep_paper_id)?.title}"?`)) {
                                        try {
                                          const res = await fetch(`${API_BASE}/api/papers/merge`, {
                                            method: 'POST',
                                            headers: { 'Content-Type': 'application/json' },
                                            body: JSON.stringify({
                                              keep_paper_id: compReport.preferred_keep_paper_id,
                                              drop_paper_id: row.paper_id,
                                              delete_drop_pdf: true
                                            })
                                          });
                                          if (res.ok) {
                                            fetchPapers();
                                            alert('Merged successfully!');
                                          }
                                        } catch (err) {
                                          alert(err);
                                        }
                                      }
                                    }}
                                  >
                                    Merge Into Preferred
                                  </button>
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        ) : activeView === 'broken' ? (
          <div className="maintenance-view" style={{ padding: '20px', overflowY: 'auto', height: '100%' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px', borderBottom: '1px solid var(--border-color)', paddingBottom: '10px' }}>
              <h2>Papers with Broken/Incomplete Metadata</h2>
              <button className="btn" onClick={fetchMaintenanceCounts}>Refresh Audit</button>
            </div>
            {brokenPapers.length === 0 ? (
              <div className="empty-state">
                <span className="empty-state-icon">✓</span>
                <p>No broken or incomplete records found in your library index!</p>
              </div>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Title / ID</th>
                    <th>Missing / Broken Fields</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {brokenPapers.map(item => (
                    <tr key={item.paper_id}>
                      <td>
                        <div style={{ fontWeight: '500' }}>{item.title}</div>
                        <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>ID: {item.paper_id}</div>
                      </td>
                      <td>
                        <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
                          {item.issues.map(issue => (
                            <span key={issue} style={{ backgroundColor: 'rgba(220, 38, 38, 0.08)', color: 'var(--danger-color)', border: '1px solid rgba(220, 38, 38, 0.15)', borderRadius: '4px', padding: '2px 6px', fontSize: '11px', fontWeight: '500' }}>
                              {issue}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td>
                        <button className="btn btn-primary" style={{ padding: '4px 10px', fontSize: '12.5px' }} onClick={() => startWizardRepair(item)}>
                          Repair Metadata
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        ) : activeView === 'batch' ? (
          <div className="batch-view" style={{ padding: '20px', overflowY: 'auto', height: '100%' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px', borderBottom: '1px solid var(--border-color)', paddingBottom: '10px' }}>
              <h2>Batch Operations</h2>
            </div>
            
            <div className="batch-grid">
              {/* Card 1: Batch PDF Directory Auto-Ingestion */}
              <div className="batch-card">
                <h3>Batch PDF Auto-Ingestion</h3>
                <p className="batch-description">
                  Scan a directory in your library for untracked PDF files, extract text to find DOIs, and automatically ingest them if resolved with high confidence.
                </p>
                
                <div className="form-group" style={{ marginBottom: '15px' }}>
                  <label htmlFor="batch-folder-select" style={{ display: 'block', marginBottom: '5px', fontWeight: '500' }}>Directory to Scan:</label>
                  <select
                    id="batch-folder-select"
                    className="select-input"
                    value={batchFolder}
                    onChange={(e) => setBatchFolder(e.target.value)}
                    style={{ width: '100%', padding: '8px', borderRadius: '4px', border: '1px solid var(--border-color)', backgroundColor: 'var(--bg-secondary)', color: 'var(--text-primary)' }}
                  >
                    <option value="articles">articles/ (Library Root)</option>
                    {folders.map(f => (
                      <option key={f} value={`articles/${f}`}>{`articles/${f}/`}</option>
                    ))}
                  </select>
                </div>
                
                <div className="form-group checkbox-group" style={{ marginBottom: '15px' }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={batchRecursive}
                      onChange={(e) => setBatchRecursive(e.target.checked)}
                    />
                    Scan recursively (include subfolders)
                  </label>
                </div>
                
                <button
                  className="btn btn-primary"
                  onClick={handleBatchDirectoryImport}
                  disabled={isBatchDirectoryLoading}
                  style={{ width: '100%', display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '8px' }}
                >
                  {isBatchDirectoryLoading ? (
                    <>
                      <div className="spinner-small" style={{ width: '14px', height: '14px', border: '2px solid var(--border-color)', borderTopColor: 'var(--text-primary)', borderRadius: '50%', animation: 'spin 1s linear infinite' }}></div> Scanning & Ingesting...
                    </>
                  ) : (
                    'Scan & Auto-Ingest'
                  )}
                </button>
                
                {batchDirectoryResult && (
                  <div className="batch-result-panel" style={{ marginTop: '20px', padding: '15px', borderRadius: '6px', backgroundColor: 'var(--bg-tertiary)', border: '1px solid var(--border-color)' }}>
                    <h4 style={{ marginBottom: '10px' }}>Results Summary</h4>
                    <div className="result-stats" style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '10px', marginBottom: '15px', textAlign: 'center' }}>
                      <div className="stat-item" style={{ padding: '8px', backgroundColor: 'var(--bg-secondary)', borderRadius: '4px' }}>
                        <span className="stat-val" style={{ display: 'block', fontSize: '18px', fontWeight: 'bold' }}>{batchDirectoryResult.total_found}</span>
                        <span className="stat-label" style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>Found</span>
                      </div>
                      <div className="stat-item" style={{ padding: '8px', backgroundColor: 'var(--bg-secondary)', borderRadius: '4px' }}>
                        <span className="stat-val" style={{ display: 'block', fontSize: '18px', fontWeight: 'bold' }}>{batchDirectoryResult.already_indexed}</span>
                        <span className="stat-label" style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>Already Indexed</span>
                      </div>
                      <div className="stat-item success" style={{ padding: '8px', backgroundColor: 'rgba(5, 150, 105, 0.08)', color: 'var(--success-color)', borderRadius: '4px' }}>
                        <span className="stat-val" style={{ display: 'block', fontSize: '18px', fontWeight: 'bold' }}>{batchDirectoryResult.imported.length}</span>
                        <span className="stat-label" style={{ fontSize: '11px' }}>Imported</span>
                      </div>
                      <div className="stat-item warning" style={{ padding: '8px', backgroundColor: 'rgba(217, 119, 6, 0.08)', color: 'var(--text-secondary)', borderRadius: '4px' }}>
                        <span className="stat-val" style={{ display: 'block', fontSize: '18px', fontWeight: 'bold' }}>{batchDirectoryResult.skipped.length}</span>
                        <span className="stat-label" style={{ fontSize: '11px' }}>Skipped</span>
                      </div>
                    </div>
                    
                    {batchDirectoryResult.imported.length > 0 && (
                      <div className="result-section" style={{ marginBottom: '15px' }}>
                        <h5 style={{ fontSize: '12px', fontWeight: '600', marginBottom: '5px' }}>Successfully Imported:</h5>
                        <ul className="result-list success-list" style={{ listStyle: 'none', paddingLeft: '0', maxHeight: '150px', overflowY: 'auto', fontSize: '12.5px' }}>
                          {batchDirectoryResult.imported.map((item, idx) => (
                            <li key={idx} style={{ padding: '4px 0', borderBottom: '1px solid rgba(0,0,0,0.05)' }}>
                              <strong>{item.key}</strong>: {item.title}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    
                    {batchDirectoryResult.skipped.length > 0 && (
                      <div className="result-section">
                        <h5 style={{ fontSize: '12px', fontWeight: '600', marginBottom: '5px' }}>Skipped PDFs (No DOI or metadata resolved):</h5>
                        <ul className="result-list skipped-list" style={{ listStyle: 'none', paddingLeft: '0', maxHeight: '150px', overflowY: 'auto', fontSize: '12.5px', color: 'var(--text-secondary)' }}>
                          {batchDirectoryResult.skipped.map((item, idx) => (
                            <li key={idx} style={{ padding: '4px 0', borderBottom: '1px solid rgba(0,0,0,0.05)' }}>
                              <span className="filename" style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', display: 'block', wordBreak: 'break-all' }}>{item.filename}</span>
                              <span className="reason" style={{ fontSize: '11px', color: 'var(--danger-color)' }}>({item.reason})</span>
                            </li>
                          ))}
                        </ul>
                        <p className="batch-note" style={{ fontSize: '11px', marginTop: '10px', fontStyle: 'italic', color: 'var(--text-secondary)' }}>
                          Note: Skipped files can be added manually using the standard <strong>Upload PDF</strong> or <strong>Untracked PDFs</strong> repair wizard.
                        </p>
                      </div>
                    )}
                  </div>
                )}
              </div>
              
              {/* Card 2: Batch BibTeX Import */}
              <div className="batch-card">
                <h3>Batch BibTeX Import</h3>
                <p className="batch-description">
                  Paste one or more BibTeX reference entries below to import them into your library as Reference-Only entries.
                </p>
                
                <div className="form-group" style={{ marginBottom: '15px' }}>
                  <label htmlFor="batch-bibtex-textarea" style={{ display: 'block', marginBottom: '5px', fontWeight: '500' }}>BibTeX Entries:</label>
                  <textarea
                    id="batch-bibtex-textarea"
                    className="textarea-input"
                    rows="10"
                    placeholder={`@article{example2026,
  author = {Author, A. and Scientist, B.},
  title = {A Landmark Paper in Science},
  journal = {Journal of Research},
  year = {2026},
  doi = {10.1000/xyz123}
}`}
                    value={batchBibtexText}
                    onChange={(e) => setBatchBibtexText(e.target.value)}
                    style={{ width: '100%', padding: '10px', borderRadius: '4px', border: '1px solid var(--border-color)', backgroundColor: 'var(--bg-secondary)', color: 'var(--text-primary)', fontFamily: 'var(--font-mono)', fontSize: '12px', resize: 'vertical' }}
                  ></textarea>
                </div>
                
                <button
                  className="btn btn-primary"
                  onClick={handleBatchBibtexImport}
                  disabled={isBatchBibtexLoading}
                  style={{ width: '100%', display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '8px' }}
                >
                  {isBatchBibtexLoading ? (
                    <>
                      <div className="spinner-small" style={{ width: '14px', height: '14px', border: '2px solid var(--border-color)', borderTopColor: 'var(--text-primary)', borderRadius: '50%', animation: 'spin 1s linear infinite' }}></div> Importing References...
                    </>
                  ) : (
                    'Batch Import References'
                  )}
                </button>
                
                {batchBibtexResult && (
                  <div className="batch-result-panel" style={{ marginTop: '20px', padding: '15px', borderRadius: '6px', backgroundColor: 'var(--bg-tertiary)', border: '1px solid var(--border-color)' }}>
                    <h4 style={{ marginBottom: '10px' }}>Results Summary</h4>
                    <div className="result-stats" style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '10px', marginBottom: '15px', textAlign: 'center' }}>
                      <div className="stat-item" style={{ padding: '8px', backgroundColor: 'var(--bg-secondary)', borderRadius: '4px' }}>
                        <span className="stat-val" style={{ display: 'block', fontSize: '18px', fontWeight: 'bold' }}>{batchBibtexResult.total_parsed}</span>
                        <span className="stat-label" style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>Parsed</span>
                      </div>
                      <div className="stat-item success" style={{ padding: '8px', backgroundColor: 'rgba(5, 150, 105, 0.08)', color: 'var(--success-color)', borderRadius: '4px' }}>
                        <span className="stat-val" style={{ display: 'block', fontSize: '18px', fontWeight: 'bold' }}>{batchBibtexResult.imported.length}</span>
                        <span className="stat-label" style={{ fontSize: '11px' }}>Imported</span>
                      </div>
                      <div className="stat-item warning" style={{ padding: '8px', backgroundColor: 'rgba(217, 119, 6, 0.08)', color: 'var(--text-secondary)', borderRadius: '4px' }}>
                        <span className="stat-val" style={{ display: 'block', fontSize: '18px', fontWeight: 'bold' }}>{batchBibtexResult.skipped.length}</span>
                        <span className="stat-label" style={{ fontSize: '11px' }}>Skipped / Dups</span>
                      </div>
                    </div>
                    
                    {batchBibtexResult.imported.length > 0 && (
                      <div className="result-section" style={{ marginBottom: '15px' }}>
                        <h5 style={{ fontSize: '12px', fontWeight: '600', marginBottom: '5px' }}>Successfully Imported:</h5>
                        <ul className="result-list success-list" style={{ listStyle: 'none', paddingLeft: '0', maxHeight: '150px', overflowY: 'auto', fontSize: '12.5px' }}>
                          {batchBibtexResult.imported.map((item, idx) => (
                            <li key={idx} style={{ padding: '4px 0', borderBottom: '1px solid rgba(0,0,0,0.05)' }}>
                              <strong>{item.key}</strong>: {item.title}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    
                    {batchBibtexResult.skipped.length > 0 && (
                      <div className="result-section">
                        <h5 style={{ fontSize: '12px', fontWeight: '600', marginBottom: '5px' }}>Skipped (Duplicate keys/DOIs/titles, or parse failure):</h5>
                        <ul className="result-list skipped-list" style={{ listStyle: 'none', paddingLeft: '0', maxHeight: '150px', overflowY: 'auto', fontSize: '12.5px', color: 'var(--text-secondary)' }}>
                          {batchBibtexResult.skipped.map((item, idx) => (
                            <li key={idx} style={{ padding: '4px 0', borderBottom: '1px solid rgba(0,0,0,0.05)' }}>
                              <strong className="filename">{item.key}</strong>: {item.title}
                              <span className="reason" style={{ fontSize: '11px', color: 'var(--danger-color)' }}> ({item.reason})</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        ) : (
          /* Markdown Notes Editor view */
          <div className="notes-view">
            <div className="notes-header">
              <input 
                type="text" 
                className="note-title-input" 
                value={noteTitle} 
                onChange={(e) => {
                  const val = e.target.value;
                  setNoteTitle(val);
                  const lines = noteContent.split('\n');
                  if (lines.length > 0 && lines[0].startsWith('# ')) {
                    lines[0] = `# ${val}`;
                    setNoteContent(lines.join('\n'));
                  }
                }} 
                placeholder="Note Title"
              />
              <div className="actions-container">
                <button className="btn btn-primary" onClick={handleSaveNote}>Save Note</button>
                <button className="btn" onClick={() => setActiveView('library')}>Close</button>
              </div>
            </div>

            <div className="note-editor-panes">
              <div className="note-editor-pane">
                <textarea 
                  className="note-editor-textarea" 
                  value={noteContent}
                  onChange={(e) => {
                    const val = e.target.value;
                    setNoteContent(val);
                    const lines = val.split('\n');
                    if (lines.length > 0 && lines[0].startsWith('# ')) {
                      const parsedTitle = lines[0].substring(2).trim();
                      if (parsedTitle && parsedTitle !== noteTitle) {
                        setNoteTitle(parsedTitle);
                      }
                    }
                  }}
                  placeholder="# Note Title&#10;&#10;Write markdown notes here. Reference papers by citing their BibTeX key like @brandani2013quantifying."
                />
              </div>
              <div 
                className="note-preview-pane"
                dangerouslySetInnerHTML={{ __html: renderNotePreview(noteContent) }}
              />
            </div>
          </div>
        )}
      </div>

      {/* 3. Details Panel (Right Sidebar) */}
      {selectedPaper && paperDetails && (
        <div className="details-panel">
          <div className="details-header">
            <h3>Item Details</h3>
            <button className="close-btn" onClick={() => { setSelectedPaper(null); setPaperDetails(null); }}>
              ✕
            </button>
          </div>

          <div className="details-content">
            <div className="details-row">
              <label>Title</label>
              <span>{paperDetails.title}</span>
            </div>
            
            <div className="details-row">
              <label>Authors</label>
              <span style={{ color: 'var(--text-secondary)' }}>{paperDetails.authors}</span>
            </div>

            <div className="details-row">
              <label>Year & Venue</label>
              <span>{paperDetails.year} — {paperDetails.venue || 'n/a'}</span>
            </div>

            {/* Relationship cards */}
            {paperDetails.record_payload?.match_status === 'matched_supplement' && (
              <div className="details-row" style={{ backgroundColor: 'var(--accent-light)', border: '1px solid var(--accent-color)', borderRadius: '6px', padding: '10px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
                <span style={{ fontWeight: '650', fontSize: '13px', display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--text-primary)' }}>
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path></svg>
                  Linked Supplement PDF
                </span>
                <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                  This file is linked as a supplement to:
                </span>
                <span 
                  className="citation-link" 
                  onClick={() => selectPaper({ paper_id: paperDetails.record_payload.supplement_parent?.paper_id })}
                  style={{ fontSize: '12.5px', fontWeight: '500', cursor: 'pointer', textDecoration: 'underline', color: 'var(--accent-text)' }}
                >
                  {paperDetails.record_payload.supplement_parent?.resolved_title || paperDetails.record_payload.supplement_parent?.title_query || 'Parent Paper'}
                </span>
              </div>
            )}

            {supplements.length > 0 && (
              <div className="details-row">
                <label>Linked Supplements</label>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginTop: '4px' }}>
                  {supplements.map(supp => (
                    <div 
                      key={supp.paper_id} 
                      className="alert alert-info" 
                      style={{ margin: 0, padding: '8px 12px', fontSize: '12.5px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', backgroundColor: '#f0f9ff', border: '1px solid #bae6fd', borderRadius: '6px' }}
                    >
                      <span 
                        className="citation-link" 
                        onClick={() => selectPaper(supp)}
                        style={{ cursor: 'pointer', textDecoration: 'underline', color: '#0284c7', fontWeight: '500', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '160px' }}
                        title={supp.filename || 'Supplement'}
                      >
                        {supp.filename || 'Supplement'}
                      </span>
                      <a 
                        className="btn" 
                        href={`${API_BASE}/api/pdf/${supp.paper_id}/${encodeURIComponent(supp.filename || 'supplement.pdf')}`} 
                        target="_blank" 
                        rel="noreferrer" 
                        style={{ padding: '2px 8px', fontSize: '11px', height: '24px', backgroundColor: '#e0f2fe', borderColor: '#bae6fd', color: '#0369a1' }}
                      >
                        View
                      </a>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {paperDetails.doi && (
              <div className="details-row">
                <label>DOI</label>
                <span>{paperDetails.doi}</span>
              </div>
            )}

            <div className="details-row">
              <label>BibTeX Key</label>
              <code style={{ color: 'var(--accent-color)' }}>{paperDetails.bibtex_key || '(none)'}</code>
            </div>

            {paperDetails.abstract && (
              <div className="details-row">
                <label>Abstract</label>
                <span style={{ fontSize: '12.5px', color: 'var(--text-secondary)' }}>{paperDetails.abstract}</span>
              </div>
            )}

            {/* In-place BibTeX editor */}
            <div className="details-row">
              <label>BibTeX Entry</label>
              {isEditingBibtex ? (
                <>
                  <textarea 
                    value={bibtexEditText}
                    onChange={(e) => setBibtexEditText(e.target.value)}
                    style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', minHeight: '180px' }}
                  />
                  <div style={{ display: 'flex', gap: '10px', marginTop: '5px' }}>
                    <button className="btn btn-primary" onClick={handleUpdateBibtex} style={{ padding: '4px 10px', fontSize: '12px' }}>Save</button>
                    <button className="btn" onClick={() => setIsEditingBibtex(false)} style={{ padding: '4px 10px', fontSize: '12px' }}>Cancel</button>
                  </div>
                </>
              ) : (
                <button className="btn" onClick={() => setIsEditingBibtex(true)} style={{ fontSize: '12px', padding: '6px' }}>Edit BibTeX</button>
              )}
            </div>

            {/* Folder Location & Move */}
            {paperDetails.pdf_path && (
              <div className="details-row">
                <label>Folder Location</label>
                <div style={{ display: 'flex', gap: '8px', flexDirection: 'column' }}>
                  <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                    {paperDetails.pdf_path.split('/').slice(0, -1).join('/') || '(root)'}
                  </span>
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <select 
                      className="select-style"
                      style={{ flexGrow: 1, height: '30px' }}
                      defaultValue={paperDetails.pdf_path.split('/').slice(0, -1).join('/') || ''}
                      id="move-folder-select"
                    >
                      <option value="">(root)</option>
                      {folders.map(f => (
                        <option key={f} value={f}>{f}</option>
                      ))}
                    </select>
                    <button 
                      className="btn" 
                      style={{ padding: '0 12px', height: '30px', fontSize: '12px' }}
                      onClick={async () => {
                        const selectEl = document.getElementById('move-folder-select');
                        const dest = selectEl && selectEl.value ? `articles/${selectEl.value}` : 'articles';
                        await handleMovePaper(dest);
                      }}
                    >
                      Move
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* Link as supplement */}
            {paperDetails.record_payload?.match_status !== 'matched_supplement' && (
              <div className="details-row">
                <label>Maintenance</label>
                <button 
                  className="btn" 
                  onClick={() => {
                    setLinkSupplementSearchQuery('');
                    setLinkSupplementSelectedParent(null);
                    setShowLinkSupplementModal(true);
                  }}
                  style={{ fontSize: '12.5px', padding: '6px', justifyContent: 'center', width: '100%' }}
                >
                  Link as Supplement...
                </button>
              </div>
            )}

            {/* Merge record */}
            <div className="details-row">
              {paperDetails.record_payload?.match_status === 'matched_supplement' && <label>Maintenance</label>}
              <button 
                className="btn" 
                onClick={() => {
                  setMergeSearchQuery('');
                  setMergeSelectedPaper(null);
                  setDeleteDropPdf(false);
                  setShowMergeModal(true);
                }}
                style={{ fontSize: '12.5px', padding: '6px', justifyContent: 'center', width: '100%' }}
              >
                Merge with duplicate...
              </button>
            </div>

            {/* Actions */}
            <div className="actions-grid">
              {paperDetails.pdf_path && (
                <a 
                  className="btn btn-primary" 
                  href={`${API_BASE}/api/pdf/${paperDetails.paper_id}/${encodeURIComponent(paperDetails.filename || 'paper.pdf')}`} 
                  target="_blank" 
                  rel="noreferrer" 
                  style={{ justifyContent: 'center' }}
                >
                  View PDF
                </a>
              )}
              {paperDetails.text_path && (
                <a 
                  className="btn" 
                  href={`${API_BASE}/api/text/${paperDetails.paper_id}/${encodeURIComponent((paperDetails.filename || 'paper').replace(/\.pdf$/i, '') + '.txt')}`} 
                  target="_blank" 
                  rel="noreferrer" 
                  style={{ justifyContent: 'center' }}
                >
                  Read Text
                </a>
              )}
              {supplements.map((supp, sIdx) => (
                <a 
                  key={supp.paper_id}
                  className="btn" 
                  href={`${API_BASE}/api/pdf/${supp.paper_id}/${encodeURIComponent(supp.filename || 'supplement.pdf')}`} 
                  target="_blank" 
                  rel="noreferrer" 
                  style={{ justifyContent: 'center', backgroundColor: '#e0f2fe', borderColor: '#bae6fd', color: '#0369a1', fontWeight: '500' }}
                >
                  View Supp {supplements.length > 1 ? `#${sIdx + 1}` : ''}
                </a>
              ))}
            </div>

            <button className="btn" onClick={() => handleDeletePaper(true)} style={{ color: 'var(--danger-color)', borderColor: 'var(--danger-color)', width: '100%', justifyContent: 'center', marginTop: '10px' }}>
              Delete Record & PDF
            </button>

            {/* Citation Render box */}
            <div className="citation-box">
              <div className="citation-header">
                <label>Quick Citation</label>
                <select className="select-style" value={selectedStyle} onChange={e => setSelectedStyle(e.target.value)}>
                  <option value="nature">Nature</option>
                  <option value="nar">NAR</option>
                  <option value="nlm">NLM</option>
                  <option value="acs">ACS</option>
                  <option value="ieee">IEEE</option>
                </select>
              </div>
              <p className="citation-text">{renderedCitation}</p>
              <button 
                className="copy-btn" 
                style={{ marginTop: '8px' }}
                onClick={() => {
                  navigator.clipboard.writeText(renderedCitation);
                  alert('Citation copied!');
                }}
              >
                Copy to Clipboard
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 4. Ingestion Wizard Modal */}
      {showWizardModal && (
        <div className="modal-overlay">
          <div className="modal-content" style={{ maxWidth: '750px', width: '95%' }}>
            <div className="modal-header">
              <h3>
                {wizardMode === 'existing' && 'Add PDF Paper'}
                {wizardMode === 'ref' && 'Add Reference-Only Entry'}
                {wizardMode === 'repair' && 'Repair Paper Metadata'}
              </h3>
              <button 
                className="close-btn" 
                onClick={closeWizardModal}
              >
                ✕
              </button>
            </div>
            
            <div className="modal-body">
              {/* Step indicator */}
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '15px', padding: '0 10px' }}>
                {wizardMode === 'existing' && (
                  <>
                    <div style={{ fontWeight: wizardStep === 0 ? '700' : '400', color: wizardStep === 0 ? 'var(--accent-color)' : 'var(--text-secondary)' }}>1. Select File</div>
                    <div style={{ fontWeight: wizardStep === 1 ? '700' : '400', color: wizardStep === 1 ? 'var(--accent-color)' : 'var(--text-secondary)' }}>2. DOI Resolution</div>
                    <div style={{ fontWeight: wizardStep === 2 ? '700' : '400', color: wizardStep === 2 ? 'var(--accent-color)' : 'var(--text-secondary)' }}>3. Semantic Scholar</div>
                    <div style={{ fontWeight: wizardStep === 3 ? '700' : '400', color: wizardStep === 3 ? 'var(--accent-color)' : 'var(--text-secondary)' }}>4. Manual Paste</div>
                  </>
                )}
                {wizardMode === 'ref' && (
                  <>
                    <div style={{ fontWeight: wizardStep === 2 ? '700' : '400', color: wizardStep === 2 ? 'var(--accent-color)' : 'var(--text-secondary)' }}>1. Semantic Scholar</div>
                    <div style={{ fontWeight: wizardStep === 3 ? '700' : '400', color: wizardStep === 3 ? 'var(--accent-color)' : 'var(--text-secondary)' }}>2. Manual Paste</div>
                  </>
                )}
                {wizardMode === 'repair' && (
                  <>
                    <div style={{ fontWeight: wizardStep === 1 ? '700' : '400', color: wizardStep === 1 ? 'var(--accent-color)' : 'var(--text-secondary)' }}>1. DOI Resolution</div>
                    <div style={{ fontWeight: wizardStep === 2 ? '700' : '400', color: wizardStep === 2 ? 'var(--accent-color)' : 'var(--text-secondary)' }}>2. Semantic Scholar</div>
                    <div style={{ fontWeight: wizardStep === 3 ? '700' : '400', color: wizardStep === 3 ? 'var(--accent-color)' : 'var(--text-secondary)' }}>3. Manual Paste</div>
                  </>
                )}
              </div>
              
              {wizardMode === 'repair' && wizardRepairPaper && (
                <div style={{ 
                  backgroundColor: 'var(--accent-light)', 
                  border: '1px solid var(--accent-color)', 
                  borderRadius: '6px', 
                  padding: '12px 15px', 
                  marginBottom: '18px',
                  fontSize: '13px',
                  lineHeight: '1.5'
                }}>
                  <div style={{ fontWeight: '600', marginBottom: '8px', color: 'var(--text-primary)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ color: 'var(--text-primary)' }}>Paper Being Repaired:</span>
                    <button 
                      className="btn btn-danger" 
                      style={{ 
                        padding: '4px 8px', 
                        fontSize: '11.5px', 
                        backgroundColor: '#fee2e2', 
                        color: '#991b1b', 
                        border: '1px solid #fca5a5',
                        borderRadius: '4px',
                        cursor: 'pointer',
                        fontWeight: '600'
                      }}
                      onClick={async () => {
                        const confirmDelete = confirm("Are you sure you want to delete this record? This action cannot be undone.");
                        if (!confirmDelete) return;
                        
                        const deletePdf = confirm("Do you also want to delete the associated PDF file from disk?\n\nClick 'OK' to delete both the record and the PDF file.\nClick 'Cancel' to delete ONLY the library record (the PDF will remain on disk).");
                        
                        try {
                          setIsWizardProcessing(true);
                          const res = await fetch(`${API_BASE}/api/papers/${wizardRepairPaper.paper_id}?delete_pdf=${deletePdf}`, {
                            method: 'DELETE'
                          });
                          if (res.ok) {
                            alert("Record successfully deleted!");
                            closeWizardModal();
                            setSelectedPaper(null);
                            setPaperDetails(null);
                            fetchPapers();
                            fetchMaintenanceCounts();
                          } else {
                            const err = await res.json();
                            alert(`Failed to delete record: ${err.detail}`);
                          }
                        } catch (err) {
                          alert(`Error deleting record: ${err}`);
                        } finally {
                          setIsWizardProcessing(false);
                        }
                      }}
                    >
                      Delete Entire Record...
                    </button>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: '95px 1fr', gap: '4px 8px', color: 'var(--text-primary)' }}>
                    <div style={{ color: 'var(--text-secondary)', fontWeight: '600' }}>Paper ID:</div>
                    <div style={{ fontFamily: 'var(--font-mono)', wordBreak: 'break-all' }}>{wizardRepairPaper.paper_id}</div>
                    
                    <div style={{ color: 'var(--text-secondary)', fontWeight: '600' }}>Current Title:</div>
                    <div style={{ fontWeight: '500' }}>{wizardRepairPaper.title || 'Untitled'}</div>
                    
                    <div style={{ color: 'var(--text-secondary)', fontWeight: '600' }}>Current Path:</div>
                    <div style={{ fontFamily: 'var(--font-mono)', wordBreak: 'break-all' }}>
                      {wizardRepairPaper.pdf_path ? (
                        <span>
                          <code>{wizardRepairPaper.pdf_path}</code>
                        </span>
                      ) : (
                        <span style={{ color: 'var(--text-secondary)', fontStyle: 'italic' }}>Reference-only (no PDF)</span>
                      )}
                    </div>

                    <div style={{ color: 'var(--text-secondary)', fontWeight: '600' }}>Issues:</div>
                    <div>
                      <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
                        {wizardRepairPaper.issues?.map(issue => (
                          <span key={issue} style={{ backgroundColor: 'rgba(220, 38, 38, 0.08)', color: 'var(--danger-color)', border: '1px solid rgba(220, 38, 38, 0.15)', borderRadius: '4px', padding: '1px 5px', fontSize: '11px', fontWeight: '500' }}>
                            {issue}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              )}
              
              {isWizardProcessing && (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '30px', gap: '10px', color: 'var(--accent-color)', fontWeight: '600' }}>
                  <div className="spinner" style={{ width: '30px', height: '30px', borderRadius: '50%', border: '3px solid var(--accent-light)', borderTopColor: 'var(--accent-color)', animation: 'spin 1s linear infinite' }}></div>
                  <span>Processing... Please wait.</span>
                </div>
              )}

              {!isWizardProcessing && (
                <>
                  {wizardScanData?.is_duplicate ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '15px' }}>
                      <div className="alert alert-warning" style={{ backgroundColor: 'var(--accent-light)', border: '1px solid var(--accent-color)', borderRadius: '6px', padding: '12px', color: 'var(--text-primary)' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontWeight: 'bold', marginBottom: '6px' }}>
                          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>
                          <span>Duplicate PDF Detected</span>
                        </div>
                        <p style={{ margin: 0, fontSize: '13px' }}>
                          This file (SHA256: <code>{wizardScanData.sha256 ? wizardScanData.sha256.substring(0, 16) : ''}...</code>) is already indexed in your library.
                        </p>
                      </div>

                      <div style={{ padding: '15px', border: '1px solid var(--border-color)', borderRadius: '6px', backgroundColor: 'var(--bg-primary)' }}>
                        <div style={{ fontWeight: '600', fontSize: '14.5px', marginBottom: '8px' }}>
                          Currently Indexed Paper Details:
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', fontSize: '13px' }}>
                          <div><strong>Title:</strong> {wizardScanData.existing_title}</div>
                          <div><strong>Current Path:</strong> <code>{wizardScanData.existing_pdf_path}</code></div>
                          <div><strong>New Path:</strong> <code>{wizardFilePath}</code></div>
                        </div>
                      </div>

                      <div style={{ fontSize: '13px', color: 'var(--text-secondary)', lineHeight: '1.5' }}>
                        Choose one of the actions below to resolve this duplicate:
                        <ul style={{ marginTop: '5px', paddingLeft: '20px' }}>
                          <li><strong>Keep at New Path & Delete Old PDF:</strong> Updates the registration in the index to point to the new location and deletes the duplicate file at the old location.</li>
                          <li><strong>Delete This Duplicate PDF:</strong> Physically deletes this new file from disk to avoid clutter.</li>
                          <li><strong>View Existing Paper:</strong> Closes this wizard and opens the metadata details of the currently indexed paper.</li>
                        </ul>
                      </div>
                    </div>
                  ) : isWizardSupplementMode ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '15px' }}>
                      <div style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                        Link this PDF (<code>{wizardFilePath || wizardScanData?.temp_filename}</code>) as a supplementary material to an existing paper.
                      </div>
                      
                      <div className="form-group">
                        <label>Search Parent Paper</label>
                        <input 
                          type="text" 
                          className="form-control" 
                          placeholder="Type title, author, or BibTeX key..."
                          value={wizardSupplementSearchQuery}
                          onChange={e => setWizardSupplementSearchQuery(e.target.value)}
                        />
                      </div>

                      {wizardSupplementCandidates.length > 0 && (
                        <div className="form-group">
                          <label>Select Parent Paper</label>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '180px', overflowY: 'auto' }}>
                            {wizardSupplementCandidates.map(c => (
                              <div 
                                key={c.paper_id} 
                                className={`candidate-item ${wizardSupplementSelectedParent?.paper_id === c.paper_id ? 'selected' : ''}`}
                                onClick={() => setWizardSupplementSelectedParent(c)}
                              >
                                <div className="candidate-title">{c.title}</div>
                                <div className="candidate-meta">{c.authors} ({c.year}) — {c.bibtex_key}</div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {wizardSupplementSelectedParent && (
                        <div style={{ padding: '12px', border: '1px solid var(--accent-color)', borderRadius: '6px', backgroundColor: 'var(--accent-light)' }}>
                          <div style={{ fontWeight: '600', color: 'var(--text-primary)' }}>Selected Parent Paper:</div>
                          <div style={{ fontWeight: '500', marginTop: '4px', fontSize: '13.5px' }}>{wizardSupplementSelectedParent.title}</div>
                          <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>{wizardSupplementSelectedParent.authors} ({wizardSupplementSelectedParent.year})</div>
                        </div>
                      )}
                    </div>
                  ) : (
                    <>
                      {/* STEP 0: Folder and file selection */}
                      {wizardStep === 0 && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '15px' }}>
                      <div className="form-group">
                        <label>Select Category Directory</label>
                        <select 
                          className="form-control" 
                          value={wizardFolder} 
                          onChange={e => {
                            setWizardFolder(e.target.value);
                            setWizardFilePath('');
                          }}
                        >
                          <option value="">(root of articles/)</option>
                          {folders.map(f => (
                            <option key={f} value={f}>{f}</option>
                          ))}
                        </select>
                      </div>

                      <div className="form-group">
                        <label>Select PDF file to ingest:</label>
                        {untrackedInFolder.length === 0 ? (
                          <div style={{ padding: '15px', border: '1px dashed var(--border-color)', borderRadius: '6px', textAlign: 'center', color: 'var(--text-secondary)' }}>
                            No untracked PDF files found in <code>articles/{wizardFolder || '(root)'}</code>.<br/>
                            Place your PDF files in this folder on your computer to see them here.
                          </div>
                        ) : (
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', maxHeight: '200px', overflowY: 'auto', border: '1px solid var(--border-color)', borderRadius: '6px', padding: '6px' }}>
                            {untrackedInFolder.map(item => (
                              <div 
                                key={item.relative_path} 
                                className={`candidate-item ${wizardFilePath === item.relative_path ? 'selected' : ''}`}
                                onClick={() => setWizardFilePath(item.relative_path)}
                                style={{ padding: '8px 12px', fontSize: '13px', display: 'flex', alignItems: 'center', gap: '8px' }}
                              >
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>
                                <span style={{ textOverflow: 'ellipsis', overflow: 'hidden', whiteSpace: 'nowrap' }}>{item.filename}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {/* STEP 1: DOI Resolution */}
                  {wizardStep === 1 && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '15px' }}>
                      {wizardScanData?.doi_found ? (
                        <>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--success-color)', fontWeight: '600' }}>
                            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>
                            <span>DOI Found: <code>{wizardScanData.doi_found}</code></span>
                          </div>
                          
                          <div style={{ padding: '12px', border: '1px solid var(--border-color)', borderRadius: '6px', backgroundColor: 'var(--bg-primary)' }}>
                            <div style={{ fontWeight: '600', fontSize: '14px', marginBottom: '4px' }}>
                              {wizardScanData.doi_metadata?.title || 'Unknown Title'}
                            </div>
                            <div style={{ fontSize: '12.5px', color: 'var(--text-secondary)' }}>
                              Authors: {
                                wizardScanData.doi_metadata?.author?.map(a => `${a.given || ''} ${a.family || ''}`).join(', ') || 'n/a'
                              }
                            </div>
                            <div style={{ fontSize: '12.5px', color: 'var(--text-secondary)', marginTop: '2px' }}>
                              Year: {wizardScanData.doi_metadata?.created?.['date-parts']?.[0]?.[0] || 'n/a'} | Venue: {wizardScanData.doi_metadata?.['container-title']?.[0] || 'n/a'}
                            </div>
                          </div>

                          <div className="form-group">
                            <label>BibTeX Entry Preview (Directly Editable):</label>
                            <textarea 
                              className="form-control" 
                              value={wizardManualBibtex}
                              onChange={e => setWizardManualBibtex(e.target.value)}
                              style={{ fontFamily: 'var(--font-mono)', minHeight: '200px', fontSize: '12.5px' }}
                            />
                          </div>
                        </>
                      ) : (
                        <div style={{ padding: '15px', border: '1px dashed var(--border-color)', borderRadius: '6px', textAlign: 'center', color: 'var(--text-secondary)' }}>
                          No DOI could be found or resolved in the PDF file.<br/>
                          Let's try searching Semantic Scholar instead.
                        </div>
                      )}
                    </div>
                  )}

                  {/* STEP 2: Semantic Scholar Search */}
                  {wizardStep === 2 && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '15px' }}>
                      <div className="form-group">
                        <label>Search Query (Title or Keywords)</label>
                        <div style={{ display: 'flex', gap: '8px' }}>
                          <input 
                            type="text" 
                            className="form-control"
                            value={wizardSearchQuery}
                            onChange={e => setWizardSearchQuery(e.target.value)}
                            onKeyDown={e => e.key === 'Enter' && handleWizardSearch()}
                            placeholder="Enter paper title or keywords..."
                          />
                          <button className="btn btn-primary" onClick={handleWizardSearch} disabled={isSearchingWizard}>
                            {isSearchingWizard ? 'Searching...' : 'Search'}
                          </button>
                        </div>
                      </div>

                      {wizardCandidates.length > 0 && (
                        <div className="form-group">
                          <label>Candidates Found (Select to load BibTeX)</label>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '180px', overflowY: 'auto' }}>
                            {wizardCandidates.map(c => (
                              <div 
                                key={c.paperId} 
                                className={`candidate-item ${wizardSelectedCandidate?.paperId === c.paperId ? 'selected' : ''}`}
                                onClick={() => handleWizardSelectCandidate(c)}
                                style={{ padding: '8px 12px' }}
                              >
                                <div style={{ fontWeight: '600', fontSize: '13px' }}>{c.title}</div>
                                <div style={{ fontSize: '11.5px', color: 'var(--text-secondary)' }}>
                                  {c.authors?.map(a => a.name).join(', ')} ({c.year}) — {c.venue || 'Unknown Venue'}
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {wizardManualBibtex && (
                        <div className="form-group">
                          <label>BibTeX Entry Preview (Directly Editable):</label>
                          <textarea 
                            className="form-control" 
                            value={wizardManualBibtex}
                            onChange={e => setWizardManualBibtex(e.target.value)}
                            style={{ fontFamily: 'var(--font-mono)', minHeight: '180px', fontSize: '12.5px' }}
                          />
                        </div>
                      )}
                    </div>
                  )}

                  {/* STEP 3: Manual BibTeX Paste */}
                  {wizardStep === 3 && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '15px' }}>
                      <div className="form-group">
                        <label>Paste BibTeX Entry Here</label>
                        <textarea 
                          className="form-control" 
                          value={wizardManualBibtex}
                          onChange={e => setWizardManualBibtex(e.target.value)}
                          placeholder="@article{key,&#10;  title={Paper Title},&#10;  author={Author, A. and Bob, B.},&#10;  year={2023}&#10;}"
                          style={{ fontFamily: 'var(--font-mono)', minHeight: '250px', fontSize: '12.5px' }}
                        />
                      </div>
                    </div>
                  )}
                </>
              )}
            </>
          )}
            </div>

            <div className="modal-footer">
              {!isWizardProcessing && (
                <>
                  {wizardScanData?.is_duplicate ? (
                    <>
                      <button className="btn" onClick={closeWizardModal}>
                        Cancel
                      </button>
                      <button className="btn" onClick={handleViewExistingDuplicate}>
                        View Existing Paper
                      </button>
                      <button 
                        className="btn" 
                        style={{ backgroundColor: '#fee2e2', color: '#991b1b', borderColor: '#fca5a5' }}
                        onClick={() => handleResolveDuplicate('delete_new_file')}
                      >
                        Delete This Duplicate PDF
                      </button>
                      <button className="btn btn-primary" onClick={() => handleResolveDuplicate('use_new_path')}>
                        Keep at New Path & Delete Old PDF
                      </button>
                    </>
                  ) : isWizardSupplementMode ? (
                    <>
                      <button className="btn" onClick={() => setIsWizardSupplementMode(false)}>
                        Back to Metadata Ingestion
                      </button>
                      <button 
                        className="btn btn-primary" 
                        onClick={handleWizardIngestAsSupplement}
                        disabled={!wizardSupplementSelectedParent}
                      >
                        Confirm Supplement Link
                      </button>
                    </>
                  ) : (
                    <>
                      {!isWizardSupplementMode && (wizardMode === 'existing' || wizardMode === 'upload') && wizardStep > 0 && (
                        <button 
                          className="btn" 
                          style={{ marginRight: 'auto', backgroundColor: 'var(--accent-light)', borderColor: 'var(--accent-color)' }}
                          onClick={() => { setIsWizardSupplementMode(true); setWizardSupplementSearchQuery(''); setWizardSupplementSelectedParent(null); }}
                        >
                          Link as Supplement instead
                        </button>
                      )}
                      {/* Back/Nav Buttons */}
                      {wizardStep === 0 && (
                        <button className="btn" onClick={closeWizardModal}>Cancel</button>
                      )}
                      {wizardStep === 1 && (
                        <>
                          {wizardMode === 'existing' && (
                            <button className="btn" onClick={() => setWizardStep(0)}>Back to File Selector</button>
                          )}
                          {wizardMode !== 'existing' && (
                            <button className="btn" onClick={closeWizardModal}>Cancel</button>
                          )}
                          {wizardScanData?.doi_found ? (
                            <button className="btn" onClick={() => setWizardStep(2)}>Skip/Use Semantic Scholar</button>
                          ) : (
                            <button className="btn btn-primary" onClick={() => setWizardStep(2)}>Proceed to Search</button>
                          )}
                        </>
                      )}
                      {wizardStep === 2 && (
                        <>
                          {wizardMode === 'ref' ? (
                            <button className="btn" onClick={closeWizardModal}>Cancel</button>
                          ) : (
                            <button className="btn" onClick={() => setWizardStep(1)}>Back to DOI</button>
                          )}
                          <button className="btn" onClick={() => setWizardStep(3)}>Try Manual Paste</button>
                        </>
                      )}
                      {wizardStep === 3 && (
                        <button className="btn" onClick={() => setWizardStep(2)}>Back to Search</button>
                      )}

                      {/* Action Confirm Buttons */}
                      {wizardStep === 0 && (
                        <button 
                          className="btn btn-primary" 
                          onClick={handleWizardScanPdf}
                          disabled={!wizardFilePath}
                        >
                          Scan & Ingest
                        </button>
                      )}
                      {wizardStep === 1 && wizardScanData?.doi_found && (
                        <button className="btn btn-primary" onClick={handleWizardConfirm}>Confirm Ingestion</button>
                      )}
                      {wizardStep === 2 && (
                        <button 
                          className="btn btn-primary" 
                          onClick={handleWizardConfirm}
                          disabled={!wizardSelectedCandidate && !wizardManualBibtex}
                        >
                          Confirm Ingestion
                        </button>
                      )}
                      {wizardStep === 3 && (
                        <button 
                          className="btn btn-primary" 
                          onClick={handleWizardConfirm}
                          disabled={!wizardManualBibtex.trim()}
                        >
                          Confirm Ingestion
                        </button>
                      )}
                    </>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* 6. Merge Papers Modal */}
      {showMergeModal && (
        <div className="modal-overlay">
          <div className="modal-content">
            <div className="modal-header">
              <h3>Merge duplicate papers</h3>
              <button className="close-btn" onClick={() => setShowMergeModal(false)}>✕</button>
            </div>
            <div className="modal-body">
              <p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                You are merging another record into <strong>{selectedPaper.title}</strong>. 
                The selected duplicate paper's entry will be dropped/deleted, and its metadata merged into this one.
              </p>
              
              <div className="form-group" style={{ marginTop: '10px' }}>
                <label>Search paper to merge/drop</label>
                <input 
                  type="text" 
                  className="form-control" 
                  placeholder="Type title, author, or BibTeX key..."
                  value={mergeSearchQuery}
                  onChange={e => setMergeSearchQuery(e.target.value)}
                />
              </div>

              {mergeCandidates.length > 0 && (
                <div className="form-group">
                  <label>Select matching paper</label>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '200px', overflowY: 'auto' }}>
                    {mergeCandidates.map(candidate => (
                      <div 
                        key={candidate.paper_id} 
                        className={`candidate-item ${mergeSelectedPaper?.paper_id === candidate.paper_id ? 'selected' : ''}`}
                        onClick={() => setMergeSelectedPaper(candidate)}
                      >
                        <div className="candidate-title">{candidate.title}</div>
                        <div className="candidate-meta">
                          {candidate.authors} ({candidate.year}) — {candidate.bibtex_key}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {mergeSelectedPaper && (
                <div style={{ marginTop: '10px', padding: '12px', border: '1px solid var(--danger-color)', borderRadius: '6px', backgroundColor: 'rgba(239, 68, 68, 0.05)' }}>
                  <div style={{ fontWeight: '600', color: 'var(--danger-color)' }}>⚠️ Paper to be dropped:</div>
                  <div style={{ fontWeight: '500', marginTop: '4px' }}>{mergeSelectedPaper.title}</div>
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>{mergeSelectedPaper.authors} ({mergeSelectedPaper.year})</div>
                  
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '12px' }}>
                    <input 
                      type="checkbox" 
                      id="delete-drop-pdf" 
                      checked={deleteDropPdf}
                      onChange={e => setDeleteDropPdf(e.target.checked)}
                    />
                    <label htmlFor="delete-drop-pdf" style={{ fontSize: '12.5px', cursor: 'pointer', fontWeight: '500' }}>
                      Delete dropped paper's PDF file (if any)
                    </label>
                  </div>
                </div>
              )}
            </div>
            <div className="modal-footer">
              <button className="btn" onClick={() => setShowMergeModal(false)}>Cancel</button>
              <button 
                className="btn btn-primary" 
                style={{ backgroundColor: 'var(--danger-color)', borderColor: 'var(--danger-color)' }}
                onClick={async () => {
                  if (!mergeSelectedPaper) {
                    alert('Please select a paper to merge.');
                    return;
                  }
                  try {
                    const res = await fetch(`${API_BASE}/api/papers/merge`, {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({
                        keep_paper_id: selectedPaper.paper_id,
                        drop_paper_id: mergeSelectedPaper.paper_id,
                        delete_drop_pdf: deleteDropPdf
                      })
                    });
                    if (res.ok) {
                      setShowMergeModal(false);
                      fetchPapers();
                      selectPaper(selectedPaper);
                      alert('Papers merged successfully!');
                    } else {
                      const err = await res.json();
                      alert(`Merge failed: ${err.detail}`);
                    }
                  } catch (err) {
                    alert(`Error merging papers: ${err}`);
                  }
                }}
                disabled={!mergeSelectedPaper}
              >
                Confirm Merge
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 7. Link as Supplement Modal */}
      {showLinkSupplementModal && (
        <div className="modal-overlay">
          <div className="modal-content">
            <div className="modal-header">
              <h3>Link as Supplement</h3>
              <button className="close-btn" onClick={() => setShowLinkSupplementModal(false)}>✕</button>
            </div>
            <div className="modal-body">
              <p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                You are linking <strong>{selectedPaper.title}</strong> as a supplement to another paper in your library.
              </p>
              
              <div className="form-group" style={{ marginTop: '10px' }}>
                <label>Search Parent Paper</label>
                <input 
                  type="text" 
                  className="form-control" 
                  placeholder="Type title, author, or BibTeX key..."
                  value={linkSupplementSearchQuery}
                  onChange={e => setLinkSupplementSearchQuery(e.target.value)}
                />
              </div>

              {linkSupplementCandidates.length > 0 && (
                <div className="form-group">
                  <label>Select Parent Paper</label>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '200px', overflowY: 'auto' }}>
                    {linkSupplementCandidates.map(candidate => (
                      <div 
                        key={candidate.paper_id} 
                        className={`candidate-item ${linkSupplementSelectedParent?.paper_id === candidate.paper_id ? 'selected' : ''}`}
                        onClick={() => setLinkSupplementSelectedParent(candidate)}
                      >
                        <div className="candidate-title">{candidate.title}</div>
                        <div className="candidate-meta">
                          {candidate.authors} ({candidate.year}) — {candidate.bibtex_key}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {linkSupplementSelectedParent && (
                <div style={{ marginTop: '10px', padding: '12px', border: '1px solid var(--accent-color)', borderRadius: '6px', backgroundColor: 'var(--accent-light)' }}>
                  <div style={{ fontWeight: '600', color: 'var(--text-primary)' }}>Selected Parent Paper:</div>
                  <div style={{ fontWeight: '500', marginTop: '4px' }}>{linkSupplementSelectedParent.title}</div>
                  <div style={{ fontSize: '12.5px', color: 'var(--text-secondary)' }}>{linkSupplementSelectedParent.authors} ({linkSupplementSelectedParent.year})</div>
                </div>
              )}
            </div>
            <div className="modal-footer">
              <button className="btn" onClick={() => setShowLinkSupplementModal(false)}>Cancel</button>
              <button 
                className="btn btn-primary" 
                onClick={handleLinkSupplement}
                disabled={!linkSupplementSelectedParent}
              >
                Confirm Link
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
