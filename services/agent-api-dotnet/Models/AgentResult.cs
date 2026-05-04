namespace InfraAdvisor.AgentApi.Models;

public record AgentResult(
    string Answer,
    List<string> Sources,
    List<string> ToolsCalled,
    string QueryDomain
);
