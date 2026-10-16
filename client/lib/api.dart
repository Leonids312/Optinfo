import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'models.dart';

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
    }
    await prefs.setString('server_base', normalized);
    base = normalized;
    token = accessToken.trim();
  }

  Future<Json?> cachedBriefing() async {
    final prefs = await SharedPreferences.getInstance();
    final value = prefs.getString('briefing_cache');
    if (value == null) return null;
    try {
      return object(jsonDecode(value));
    } catch (_) {
      return null;
    }
  }

  Future<void> cacheBriefing(Json value) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('briefing_cache', jsonEncode(value));
  }
}

class Api {
  final ConnectionStore connection;
  Api(this.connection);
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
    await call(
      '/api/${company ? 'companies' : 'items'}/${Uri.encodeComponent(id)}/bookmark',
      method: 'PUT',
      body: {'bookmarked': marked},
    );
  }
}
