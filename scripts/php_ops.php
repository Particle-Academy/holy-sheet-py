<?php

declare(strict_types=1);

/**
 * Cross-runtime OPS parity helper: run a batch of `Agent::diff` / `reduce` /
 * `equivalent` / `opSchema` / `SheetDiff::hunks` calls through the PHP
 * holy-sheet and print every result as JSON, so the Python port of
 * `HolySheet\Ops` can be compared call for call. Same autoloader and lookup
 * order as php_tobytes.php.
 *
 *   php php_ops.php <calls.json>
 *
 * The input is a JSON list of calls:
 *
 *   {"fn": "diff", "a": {...}, "b": {...}}          -> {"ops": [...]}
 *   {"fn": "reduce", "schema": {...}, "ops": ...}   -> {"schema": {...}}
 *   {"fn": "equivalent", "a": {...}, "b": {...}}    -> {"equivalent": bool}
 *   {"fn": "opSchema"}                              -> {"schema": {...}}
 *   {"fn": "hunks", "a": [...], "b": [...]}         -> {"hunks": [...]}
 *   {"fn": "nested", "leaf": x, "depth": n, "map": bool}
 *                                                   -> {"same": SheetDiff::same(v, v)}
 *
 * "nested" wraps `leaf` in `depth` arrays (lists, or maps under "k") HERE, so
 * where json_encode's depth runs out is pinned without sending that nesting
 * through JSON in either direction.
 *
 * One process for the whole batch, because each PHP start costs more than the
 * call. A call that throws reports `{"error": class, "message": ...}` instead
 * of stopping the batch.
 *
 * JSON_PRESERVE_ZERO_FRACTION keeps a PHP float a float (`1.0`, not `1`),
 * because the int/float distinction is part of what is being compared.
 */

spl_autoload_register(function (string $class): void {
    $prefix = 'HolySheet\\';
    if (strncmp($class, $prefix, strlen($prefix)) !== 0) {
        return;
    }
    $rel = substr($class, strlen($prefix));
    $root = getenv('HOLY_SHEET_PHP_SRC') ?: __DIR__.'/../../holy-sheet/src';
    $file = rtrim($root, '/').'/'.str_replace('\\', '/', $rel).'.php';
    if (is_file($file)) {
        require $file;
    }
});

if ($argc < 2) {
    fwrite(STDERR, "usage: php php_ops.php <calls.json>\n");
    exit(2);
}

if (! class_exists(\HolySheet\Ops\SheetDiff::class)) {
    fwrite(STDERR, "HolySheet\\Ops\\SheetDiff not found. Set HOLY_SHEET_PHP_SRC to the src/ directory of holy-sheet 2.3.0 or later.\n");
    exit(3);
}

/** @param array{leaf: mixed, depth: int, map: bool} $call */
function nested(array $call): mixed
{
    $value = $call['leaf'];
    for ($i = 0; $i < $call['depth']; $i++) {
        $value = $call['map'] ? ['k' => $value] : [$value];
    }

    return $value;
}

$calls = json_decode((string) file_get_contents($argv[1]), true, 512, JSON_THROW_ON_ERROR);
$results = [];

foreach ($calls as $call) {
    try {
        $results[] = match ($call['fn']) {
            'diff' => ['ops' => \HolySheet\Agent::diff($call['a'], $call['b'])],
            'reduce' => ['schema' => \HolySheet\Agent::reduce($call['schema'], $call['ops'])],
            'equivalent' => ['equivalent' => \HolySheet\Agent::equivalent($call['a'], $call['b'])],
            'opSchema' => ['schema' => \HolySheet\Agent::opSchema()],
            'hunks' => ['hunks' => \HolySheet\Ops\SheetDiff::hunks($call['a'], $call['b'])],
            'nested' => ['same' => \HolySheet\Ops\SheetDiff::same(nested($call), nested($call))],
        };
    } catch (\Throwable $e) {
        $results[] = ['error' => get_class($e), 'message' => $e->getMessage()];
    }
}

echo json_encode($results, JSON_PRESERVE_ZERO_FRACTION | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
