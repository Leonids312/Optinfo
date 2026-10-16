import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import 'models.dart';

/// Only read-only catalogue/feed data belongs here. Chat paths are never allowed.
class QueryRequest {
  final String owner;
  final String path;
  final Map<String, String> query;
  final String day;
  QueryRequest(this.owner, this.path, Map<String, String> query, this.day)
      : query = Map.unmodifiable(Map.fromEntries(
            query.entries.where((entry) => entry.value.isNotEmpty).toList()
              ..sort((a, b) => a.key.compareTo(b.key))));

  String get key => jsonEncode([path, query, day]);
  bool get allowed =>
      path == '/api/bootstrap' ||
      path == '/api/briefing' ||
      path == '/api/companies';
}

class CachedQuery {
  final QueryRequest request;
  final Json data;
  final DateTime savedAt;
  final bool invalidated;
  CachedQuery(this.request, this.data, this.savedAt,
      {this.invalidated = false});
  bool fresh(DateTime now) =>
      !invalidated &&
      !now.isBefore(savedAt) &&
      now.difference(savedAt) < const Duration(minutes: 5);
}

Json copyJson(Json data) => object(jsonDecode(jsonEncode(data)));

class LocalQueryCache {
  static const storageKey = 'optinfo_query_cache_v2';
  static const maxEntries = 48;
  static const maxBytes = 8 * 1024 * 1024;
  static const retention = Duration(days: 30);
  final DateTime Function() clock;
  String? _owner;
  final Map<String, CachedQuery> _entries = {};
  Future<void>? _initializing;
  Future<void> _writes = Future.value();

  LocalQueryCache({DateTime Function()? clock}) : clock = clock ?? DateTime.now;

  Future<void> ready(String owner) {
    if (_owner == owner) return _initializing ?? Future.value();
    _owner = owner;
    _entries.clear();
    return _initializing = _restore(owner).then((_) => _persist(owner));
  }

  Future<void> _restore(String owner) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final serialized = prefs.getString(storageKey);
      if (_owner != owner || serialized == null) return;
      if (utf8.encode(serialized).length > maxBytes) return;
      final document = object(jsonDecode(serialized));
      if (document['owner'] != owner) return;
      for (final row in objects(document['entries'])) {
        final at = DateTime.tryParse(string(row['at']));
        if (at == null || clock().difference(at) > retention) continue;
        final request = QueryRequest(
            owner,
            string(row['path']),
            object(row['query']).map((k, v) => MapEntry(k, string(v))),
            string(row['day']));
        if (!request.allowed) continue;
        _entries.putIfAbsent(
            request.key,
            () => CachedQuery(request, object(row['data']), at,
                invalidated: row['invalidated'] == true));
      }
      _trim();
    } catch (_) {
      // Storage or corrupt old data must never prevent a network read.
    } finally {
      if (_owner == owner) _initializing = null;
    }
  }

  CachedQuery? peek(QueryRequest request) {
    if (!request.allowed || request.owner != _owner) return null;
    final entry = _entries[request.key];
    if (entry == null) return null;
    if (clock().difference(entry.savedAt) > retention) {
      _entries.remove(request.key);
      return null;
    }
    // Refresh insertion order to bound the cache by least recently used entries.
    _entries.remove(request.key);
    _entries[request.key] = entry;
    return CachedQuery(request, copyJson(entry.data), entry.savedAt,
        invalidated: entry.invalidated);
  }

  Future<void> put(QueryRequest request, Json data) async {
    if (!request.allowed || _owner != request.owner) return;
    final copied = copyJson(data);
    if (utf8.encode(jsonEncode(copied)).length > maxBytes ~/ 2) return;
    _entries.remove(request.key);
    _entries[request.key] = CachedQuery(request, copied, clock());
    _trim();
    await _persist(request.owner);
  }

  void _trim() {
    _entries.removeWhere(
        (_, entry) => clock().difference(entry.savedAt) > retention);
    while (_entries.length > maxEntries ||
        (_entries.isNotEmpty && utf8.encode(_serialize()).length > maxBytes)) {
      _entries.remove(_entries.keys.first);
    }
  }

  String _serialize() => jsonEncode({
        'owner': _owner,
        'entries': _entries.values
            .map((entry) => {
                  'path': entry.request.path,
                  'query': entry.request.query,
                  'day': entry.request.day,
                  'at': entry.savedAt.toIso8601String(),
                  'invalidated': entry.invalidated,
                  'data': entry.data,
                })
            .toList()
      });

  Future<void> _persist(String owner) {
    _writes = _writes.catchError((_) {}).then((_) async {
      if (_owner != owner) return;
      try {
        final prefs = await SharedPreferences.getInstance();
        if (_owner != owner) return;
        await prefs.setString(storageKey, _serialize());
      } catch (_) {
        /* The in-memory cache still works when local storage fails. */
      }
    });
    return _writes;
  }

  Future<void> invalidate(String owner, String path) async {
    await ready(owner);
    if (_owner != owner) return;
    for (final entry in _entries.values.toList()) {
      if (entry.request.path == path) {
        _entries[entry.request.key] = CachedQuery(
            entry.request, entry.data, entry.savedAt,
            invalidated: true);
      }
    }
    await _persist(owner);
  }

  Future<void> bookmark(String owner, String id, bool marked,
      {required bool company}) async {
    await ready(owner);
    if (_owner != owner) return;
    final path = company ? '/api/companies' : '/api/briefing';
    Json? known;
    for (final entry in _entries.values) {
      if (entry.request.path != path) continue;
      for (final row in objects(entry.data['items'])) {
        if (string(row['id']) == id) known = row;
      }
    }
    for (final entry in _entries.values.toList()) {
      if (entry.request.path != path) continue;
      final rows = objects(entry.data['items']);
      for (final row in rows) {
        if (string(row['id']) == id) row['bookmarked'] = marked;
      }
      final favorites = entry.request.query['bookmarked'] == 'true';
      if (favorites) {
        if (!marked) rows.removeWhere((row) => string(row['id']) == id);
        final unfiltered = ['q', 'section', 'journal', 'company']
            .every((key) => (entry.request.query[key] ?? '').isEmpty);
        if (marked &&
            unfiltered &&
            known != null &&
            !rows.any((row) => string(row['id']) == id)) {
          rows.insert(0, {...known, 'bookmarked': true});
        }
      }
      entry.data['items'] = rows;
      _entries[entry.request.key] = CachedQuery(
          entry.request, entry.data, entry.savedAt,
          invalidated: entry.invalidated || favorites);
    }
    _trim();
    await _persist(owner);
  }
}
