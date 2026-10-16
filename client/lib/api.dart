import 'dart:async';
import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'models.dart';
import 'cache.dart';
import 'paper_media.dart';

class ApiFailure implements Exception {
  final String message;
  ApiFailure(this.message);
  @override
  String toString() => message;
}

class ConnectionStore {
  static const _vault = FlutterSecureStorage();
  String base = '';
  String token = '';
  bool get configured => base.isNotEmpty && token.isNotEmpty;
  Future<void> load() async {
    final prefs = await SharedPreferences.getInstance();
    base = prefs.getString('server_base') ?? '';
    token = await _vault.read(key: 'device_token') ?? '';
  }

  static String normalize(String input) {
    final value = input.trim().replaceFirst(RegExp(r'/+$'), '');
    final uri = Uri.tryParse(value);
    if (uri == null ||
        !uri.hasAuthority ||
        uri.host.isEmpty ||
        uri.userInfo.isNotEmpty ||
        uri.hasQuery ||
        uri.hasFragment) {
      throw ApiFailure('请输入完整的服务器 HTTPS 地址，不包含账号、参数或片段。');
    }
    final local = uri.host == 'localhost' || uri.host == '127.0.0.1';
    if (uri.scheme != 'https' &&
        !(kDebugMode && local && uri.scheme == 'http')) {
      throw ApiFailure('请使用 HTTPS 地址，以保护访问令牌和资讯。');
    }
    return value;
  }

  Future<void> save(String address, String accessToken) async {
    final normalized = normalize(address);
    if (accessToken.trim().isEmpty) throw ApiFailure('请填写设备访问令牌。');
    final prefs = await SharedPreferences.getInstance();
    // Persist the secret in the OS credential vault, never in preferences.
    await _vault.write(key: 'device_token', value: accessToken.trim());
    if (base != normalized || token != accessToken.trim()) {
      await prefs.remove('briefing_cache');
      await prefs.remove(LocalQueryCache.storageKey);
    }
    await prefs.setString('server_base', normalized);
    base = normalized;
    token = accessToken.trim();
  }
}

class Api {
  final ConnectionStore connection;
  final LocalQueryCache cache;
  final DateTime Function() clock;
  final Map<String, Future<Json>> _inflight = {};
  final List<_BookmarkChange> _changes = [];
  final Map<int, int> _activeRevisions = {};
  int _mutation = 0;
  Api(this.connection, {LocalQueryCache? cache, DateTime Function()? clock})
      : clock = clock ?? DateTime.now,
        cache = cache ?? LocalQueryCache(clock: clock);

  Future<Uint8List> figureImage(String path) => fetchPaperImage(
        base: ConnectionStore.normalize(connection.base),
        token: connection.token,
        path: path,
      );

  String get today => clock()
      .toUtc()
      .add(const Duration(hours: 8))
      .toIso8601String()
      .substring(0, 10);
  String get cacheOwner => sha256
      .convert(utf8.encode('${connection.base}\u0000${connection.token}'))
      .toString();
  QueryRequest query(String path,
          {Map<String, String> query = const {}, String? day}) =>
      QueryRequest(cacheOwner, path, query,
          day ?? (path == '/api/bootstrap' ? today : query['date'] ?? ''));
  bool owns(QueryRequest request) =>
      connection.configured && cacheOwner == request.owner;
  CachedQuery? peek(QueryRequest request) =>
      owns(request) ? cache.peek(request) : null;

  Future<CachedQuery?> cached(QueryRequest request) async {
    if (!owns(request)) return null;
    await cache.ready(request.owner);
    return peek(request);
  }

  Future<Json> fetch(QueryRequest request) {
    final key = '${request.owner}:${request.key}';
    return _inflight.putIfAbsent(
        key,
        () => _fetch(request).whenComplete(() {
              _inflight.remove(key);
            }));
  }

  Future<Json> _fetch(QueryRequest request) async {
    if (!request.allowed) throw ApiFailure('该接口不支持本地资讯缓存。');
    if (!owns(request)) throw ApiFailure('连接已更改，请重新加载。');
    await cache.ready(request.owner);
    if (!owns(request)) throw ApiFailure('连接已更改，请重新加载。');
    final revision = _mutation;
    _activeRevisions.update(revision, (count) => count + 1, ifAbsent: () => 1);
    try {
      return await _fetchAt(request, revision);
    } finally {
      final count = _activeRevisions[revision]!;
      if (count == 1) {
        _activeRevisions.remove(revision);
      } else {
        _activeRevisions[revision] = count - 1;
      }
      _pruneChanges();
    }
  }

  void _pruneChanges() {
    if (_activeRevisions.isEmpty) {
      _changes.clear();
      return;
    }
    final oldest = _activeRevisions.keys.reduce((a, b) => a < b ? a : b);
    _changes.removeWhere((change) => change.version <= oldest);
  }

  Future<Json> _fetchAt(QueryRequest request, int revision) async {
    final data = await call(request.path, query: request.query);
    if (!owns(request)) throw ApiFailure('连接已更改，请重新加载。');
    final result = copyJson(data);
    final changes = _changes
        .where((c) =>
            c.owner == request.owner &&
            c.version > revision &&
            request.path == (c.company ? '/api/companies' : '/api/briefing'))
        .toList();
    if (request.query['bookmarked'] == 'true' && changes.isNotEmpty) {
      // Membership and search filters must be re-evaluated by the server after
      // a concurrent mutation, not guessed from an outdated favorites response.
      return _fetch(request);
    }
    // An older GET may arrive after a successful bookmark PUT. Reconcile it
    // before either rendering or persisting; later GETs remain authoritative.
    for (final change in changes) {
      final path = change.company ? '/api/companies' : '/api/briefing';
      if (request.path != path) continue;
      final rows = objects(result['items']);
      for (final row in rows) {
        if (string(row['id']) == change.id) row['bookmarked'] = change.marked;
      }
      if (request.query['bookmarked'] == 'true' && !change.marked) {
        rows.removeWhere((row) => string(row['id']) == change.id);
      }
      result['items'] = rows;
    }
    await cache.put(request, result);
    if (!owns(request)) throw ApiFailure('连接已更改，请重新加载。');
    return peek(request)?.data ?? result;
  }

  Future<Json> call(
    String path, {
    String method = 'GET',
    Json? body,
    Map<String, String>? query,
  }) async {
    if (!connection.configured) throw ApiFailure('请先设置服务器地址和访问令牌。');
    var uri = Uri.parse('${connection.base}$path');
    if (query != null) {
      uri = uri.replace(
        queryParameters: Map.fromEntries(
          query.entries.where((e) => e.value.isNotEmpty),
        ),
      );
    }
    final request = http.Request(method, uri);
    request.followRedirects = false;
    request.headers.addAll({
      'Authorization': 'Bearer ${connection.token}',
      'Accept': 'application/json',
      'Content-Type': 'application/json; charset=utf-8',
    });
    if (body != null) request.body = jsonEncode(body);
    final client = http.Client();
    final isQuestion = path.endsWith('/chat') && method == 'POST';
    try {
      final response = await (() async {
        final stream = await client.send(request);
        return http.Response.fromStream(stream);
      })()
          .timeout(Duration(seconds: isQuestion ? 420 : 25));
      Json data = {};
      try {
        data = object(jsonDecode(utf8.decode(response.bodyBytes)));
      } catch (_) {
        /* Report the HTTP status below. */
      }
      if (response.statusCode == 401 || response.statusCode == 403) {
        throw ApiFailure('认证失败，请检查设备访问令牌。');
      }
      if (response.statusCode >= 300 && response.statusCode < 400) {
        throw ApiFailure('服务器返回了重定向，请填写最终 HTTPS 接口地址。');
      }
      if (response.statusCode >= 400) {
        throw ApiFailure(
          string(data['detail']).isEmpty
              ? '服务器暂时无法处理请求（${response.statusCode}）。'
              : string(data['detail']),
        );
      }
      if (data.isEmpty) throw ApiFailure('服务器返回格式不正确，请检查接口地址。');
      return data;
    } on TimeoutException {
      throw ApiFailure(isQuestion
          ? '等待回答超时，服务器分析可能仍在进行。请先刷新会话查看结果，再决定是否重新发送。'
          : '连接超时。请确认 Tailscale 已连接，然后重试。');
    } on http.ClientException {
      throw ApiFailure('无法连接服务器。请确认网络和 Tailscale 已连接。');
    } finally {
      client.close();
    }
  }

  Future<Json> bootstrap() => call('/api/bootstrap');
  Future<Json> briefing(Map<String, String> query) =>
      call('/api/briefing', query: query);
  Future<NewsItem> item(String id) async =>
      NewsItem(await call('/api/items/${Uri.encodeComponent(id)}'));
  Future<void> bookmark(String id, bool marked, {bool company = false}) async {
    final owner = cacheOwner;
    await call(
      '/api/${company ? 'companies' : 'items'}/${Uri.encodeComponent(id)}/bookmark',
      method: 'PUT',
      body: {'bookmarked': marked},
    );
    if (owner != cacheOwner) return;
    _changes.removeWhere((change) =>
        change.owner != owner ||
        (change.id == id && change.company == company));
    _changes.add(_BookmarkChange(++_mutation, owner, id, marked, company));
    _pruneChanges();
    await cache.bookmark(owner, id, marked, company: company);
  }
}

class _BookmarkChange {
  final int version;
  final String owner, id;
  final bool marked, company;
  _BookmarkChange(this.version, this.owner, this.id, this.marked, this.company);
}
