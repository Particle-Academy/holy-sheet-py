<?php

declare(strict_types=1);

/**
 * Cross-engine CSV parity helper: run a batch of `Exchange\Csv::toCsv()` calls
 * through the PHP holy-sheet and print every result as JSON, so this port's
 * CSV writer can be compared call for call. Same autoloader and lookup order
 * as php_tobytes.php.
 *
 *   php php_csv.php <calls.json>
 *
 * Input: a JSON list of `{"table": {...}, "options": {...}|null}`. Output, one
 * per call and in order: `{"text": "...", "report": {...}}`, or
 * `{"error": class, "message": ...}` when the call throws.
 */

spl_autoload_register(function (string $class): void {
    $prefix = 'HolySheet\\';
    if (strncmp($class, $prefix, strlen($prefix)) !== 0) {
        return;
    }
    $rel = substr($class, strlen($prefix));
    $root = getenv('HOLY_SHEET_PHP_SRC') ?: __DIR__ . '/../../holy-sheet/src';
    $file = rtrim($root, '/') . '/' . str_replace('\\', '/', $rel) . '.php';
    if (is_file($file)) {
        require $file;
    }
});

$calls = json_decode((string) file_get_contents($argv[1]), true, 512, JSON_THROW_ON_ERROR);
$out = [];
foreach ($calls as $call) {
    try {
        $out[] = \HolySheet\Exchange\Csv::toCsv($call['table'], $call['options'] ?? null);
    } catch (\Throwable $e) {
        $out[] = ['error' => (new \ReflectionClass($e))->getShortName(), 'message' => $e->getMessage()];
    }
}
echo json_encode($out, JSON_PRESERVE_ZERO_FRACTION | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
