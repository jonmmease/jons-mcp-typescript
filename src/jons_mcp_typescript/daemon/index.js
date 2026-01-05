import readline from 'readline';
import { PrettierManager as PrettierManagerImpl } from './prettier-manager.js';
import { ESLintManager } from './eslint-manager.js';

// Error codes mapping
const ERROR_CODES = {
  InternalError: -32000,
  ConfigNotFound: -32001,
  ParseError: -32002,
  PluginMissing: -32003,
  Timeout: -32004,
  JSONParseError: -32700,
};

const RETRYABLE_ERRORS = new Set(['InternalError', 'Timeout']);

// Manager instances (initialized on first use)
let prettierManager = null;
let eslintManager = null;

/**
 * Get or initialize Prettier manager for a project
 */
function getPrettierManager(projectRoot) {
  if (!prettierManager) {
    prettierManager = new PrettierManagerImpl(projectRoot);
  }
  return prettierManager;
}

/**
 * Get or initialize ESLint manager for a project
 */
function getESLintManager(projectRoot) {
  if (!eslintManager) {
    eslintManager = new ESLintManager(projectRoot);
  }
  return eslintManager;
}

/**
 * Wrapper for prettier manager methods
 */
class PrettierManager {
  static async format(params) {
    const { projectRoot, filepath, content } = params || {};
    if (!projectRoot || !filepath || !content) {
      throw new Error('Missing required params: projectRoot, filepath, content');
    }
    const manager = getPrettierManager(projectRoot);
    return await manager.format(filepath, content);
  }

  static async check(params) {
    const { projectRoot, filepath, content } = params || {};
    if (!projectRoot || !filepath || !content) {
      throw new Error('Missing required params: projectRoot, filepath, content');
    }
    const manager = getPrettierManager(projectRoot);
    return await manager.check(filepath, content);
  }

  static async getConfig(params) {
    const { projectRoot, filepath } = params || {};
    if (!projectRoot || !filepath) {
      throw new Error('Missing required params: projectRoot, filepath');
    }
    const manager = getPrettierManager(projectRoot);
    return await manager.getConfig(filepath);
  }
}

/**
 * Format error response according to protocol
 */
function formatError(id, errorType, message, data = {}) {
  const code = ERROR_CODES[errorType] || ERROR_CODES.InternalError;
  const retryable = RETRYABLE_ERRORS.has(errorType);

  return {
    id,
    error: {
      code,
      message,
      data: {
        type: errorType,
        retryable,
        ...data,
      },
    },
  };
}

/**
 * Route and handle requests
 */
async function handleRequest(request) {
  const { id, version, method, params } = request;

  // Validate request structure
  if (!id || !method) {
    return formatError(undefined, 'JSONParseError', 'Missing required fields: id, method');
  }

  if (version !== 1) {
    return formatError(id, 'InternalError', `Unsupported protocol version: ${version}`);
  }

  try {
    let result;

    switch (method) {
      case 'format': {
        const { projectRoot, filepath, content } = params || {};
        if (!projectRoot || !filepath || !content) {
          return formatError(id, 'InternalError', 'Missing required params: projectRoot, filepath, content');
        }
        result = await PrettierManager.format(params);
        return { id, result };
      }

      case 'check': {
        const { projectRoot, filepath, content } = params || {};
        if (!projectRoot || !filepath || !content) {
          return formatError(id, 'InternalError', 'Missing required params: projectRoot, filepath, content');
        }
        result = await PrettierManager.check(params);
        return { id, result };
      }

      case 'lint': {
        const { projectRoot, filepath, content, fix } = params || {};
        if (!projectRoot || !filepath || !content) {
          return formatError(id, 'InternalError', 'Missing required params: projectRoot, filepath, content');
        }
        const manager = getESLintManager(projectRoot);
        result = await manager.lint(filepath, content, fix);
        return { id, result };
      }

      case 'getConfig': {
        const { tool, projectRoot, filepath } = params || {};
        if (tool === 'prettier') {
          result = await PrettierManager.getConfig(params);
        } else if (tool === 'eslint') {
          if (!projectRoot || !filepath) {
            return formatError(id, 'InternalError', 'Missing required params: projectRoot, filepath');
          }
          const manager = getESLintManager(projectRoot);
          result = await manager.getConfig(filepath);
        } else {
          return formatError(id, 'InternalError', `Unknown tool: ${tool}`);
        }
        return { id, result };
      }

      case 'ping':
        return { id, result: { ok: true } };

      case 'shutdown':
        console.error('[daemon] Shutdown requested, exiting gracefully...');
        process.exit(0);

      default:
        return formatError(id, 'InternalError', `Unknown method: ${method}`);
    }
  } catch (error) {
    // Determine error type from error message or use generic InternalError
    let errorType = 'InternalError';
    let errorData = {};

    if (error.message.includes('Config')) {
      errorType = 'ConfigNotFound';
      errorData.path = error.path || '';
    } else if (error.message.includes('Parse')) {
      errorType = 'ParseError';
    } else if (error.message.includes('Plugin')) {
      errorType = 'PluginMissing';
    } else if (error.message.includes('Timeout')) {
      errorType = 'Timeout';
    }

    console.error(`[daemon] Error handling request ${id}:`, error.message);
    return formatError(id, errorType, error.message, errorData);
  }
}

/**
 * Main daemon loop - reads JSON Lines from stdin
 */
async function main() {
  // Setup orphan prevention
  process.stdin.on('end', () => {
    console.error('[daemon] Parent disconnected, exiting...');
    process.exit(0);
  });

  process.on('disconnect', () => {
    console.error('[daemon] IPC disconnected, exiting...');
    process.exit(0);
  });

  // Create readline interface for JSON Lines protocol
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    terminal: false,
  });

  // Emit ready signal
  console.log(JSON.stringify({ event: 'ready', version: 1 }));

  // Process each line as a JSON request
  rl.on('line', async (line) => {
    if (!line.trim()) {
      return; // Skip empty lines
    }

    try {
      const request = JSON.parse(line);
      const response = await handleRequest(request);
      console.log(JSON.stringify(response));
    } catch (parseError) {
      console.error('[daemon] Failed to parse JSON:', parseError.message);
      // Attempt to extract ID if possible
      const id = (() => {
        try {
          const partial = JSON.parse(line.substring(0, line.indexOf('}')));
          return partial.id;
        } catch {
          return 'unknown';
        }
      })();
      const errorResponse = formatError(id, 'JSONParseError', parseError.message);
      console.log(JSON.stringify(errorResponse));
    }
  });

  rl.on('close', () => {
    console.error('[daemon] Readline interface closed, exiting...');
    process.exit(0);
  });

  // Handle uncaught exceptions
  process.on('uncaughtException', (error) => {
    console.error('[daemon] Uncaught exception:', error);
    process.exit(1);
  });
}

// Start the daemon
main().catch((error) => {
  console.error('[daemon] Fatal error:', error);
  process.exit(1);
});
